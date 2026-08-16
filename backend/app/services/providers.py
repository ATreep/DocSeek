from __future__ import annotations

import httpx
import json
import os
from pydantic import SecretStr


class ProviderError(RuntimeError):
    """A safe provider failure that never includes request credentials."""


ROUTE_TYPES = {
    "dg_agent_route": "llm",
    "ga_agent_route": "llm",
    "pgb_agent_route": "llm",
    "entity_agent_route": "llm",
    "ai_query_route": "llm",
    "shared_embedding_route": "embedding",
}


def save_provider_secret(settings, profile_id: str, secret: str | None) -> None:
    path = settings.conf_dir / "provider-secrets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        values = {}
    if secret:
        values[profile_id] = secret
    else:
        values.pop(profile_id, None)
    path.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")
    os.chmod(path, 0o600)


def _profile_secret(settings, profile_id: str) -> str | None:
    path = settings.conf_dir / "provider-secrets.json"
    if not path.exists():
        return None
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    secret = values.get(profile_id)
    return secret if isinstance(secret, str) and secret else None


def _selected_profile(settings, route_key: str, expected_type: str) -> tuple[str, str, str] | None:
    from ..db import connect

    with connect(settings.sqlite_path) as db:
        route = db.execute("SELECT value FROM system_config WHERE key=?", (route_key,)).fetchone()
        if not route or not route["value"]:
            return None
        profile = db.execute("SELECT id,provider_type,model,base_url FROM provider_profiles WHERE id=?", (route["value"],)).fetchone()
    if not profile or profile["provider_type"] != expected_type or not profile["base_url"]:
        raise ProviderError(f"configured {expected_type} provider route is invalid")
    secret = _profile_secret(settings, profile["id"])
    if not secret:
        raise ProviderError(f"configured {expected_type} provider route has no secret")
    return profile["model"], profile["base_url"], secret


def provider_route_metadata(settings) -> dict[str, dict[str, str | None]]:
    """Record selected model routes without persisting provider secrets."""
    from ..db import connect

    with connect(settings.sqlite_path) as db:
        values = {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM system_config WHERE key IN ({})".format(",".join("?" for _ in ROUTE_TYPES)), tuple(ROUTE_TYPES))}
        profiles = {row["id"]: dict(row) for row in db.execute("SELECT id,provider_type,model,base_url FROM provider_profiles")}
    metadata: dict[str, dict[str, str | None]] = {}
    for route_key, provider_type in ROUTE_TYPES.items():
        profile = profiles.get(values.get(route_key))
        if profile:
            metadata[route_key] = {"source": "profile", "profile_id": profile["id"], "provider_type": provider_type, "model": profile["model"], "base_url": profile["base_url"]}
            continue
        model = settings.embedding_model if provider_type == "embedding" else settings.llm_model
        base_url = settings.embedding_base_url if provider_type == "embedding" else settings.llm_base_url
        api_key = settings.embedding_api_key if provider_type == "embedding" else settings.llm_api_key
        metadata[route_key] = {"source": "environment" if model and base_url and _secret(api_key) else "local-fallback", "profile_id": None, "provider_type": provider_type, "model": model, "base_url": base_url}
    return metadata


def probe_provider_profile(settings, profile_id: str) -> dict[str, str | int | bool]:
    from ..db import connect

    with connect(settings.sqlite_path) as db:
        profile = db.execute("SELECT id,provider_type,model,base_url FROM provider_profiles WHERE id=?", (profile_id,)).fetchone()
    if not profile or not profile["base_url"]:
        raise ProviderError("provider profile is missing or has no base URL")
    secret = _profile_secret(settings, profile_id)
    if not secret:
        raise ProviderError("provider profile has no secret")
    provider = OpenAIChatProvider(profile["model"], profile["base_url"], secret) if profile["provider_type"] == "llm" else OpenAIEmbeddingProvider(profile["model"], profile["base_url"], secret)
    try:
        if profile["provider_type"] == "llm":
            # Reasoning models can consume a short budget before emitting visible content.
            provider.complete([{"role": "user", "content": "Reply with OK."}], temperature=0, max_tokens=32)
            return {"ready": True, "provider_type": "llm", "model": profile["model"]}
        vectors = provider.embed(["DocSeek provider health check"])
        return {"ready": True, "provider_type": "embedding", "model": profile["model"], "dimensions": len(vectors[0]) if vectors else 0}
    finally:
        provider.close()


def _secret(value: str | SecretStr | None) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value or ""


PROVIDER_TIMEOUT_SECONDS = 300.0


class _OpenAICompatibleProvider:
    endpoint = ""

    def __init__(self, model: str, base_url: str, api_key: str | SecretStr, *, client: httpx.Client | None = None, timeout: float = PROVIDER_TIMEOUT_SECONDS):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = _secret(api_key)
        self.client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, payload: dict) -> dict:
        try:
            response = self.client.post(
                f"{self.base_url}/{self.endpoint}",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderError("provider request timed out") from exc
        except (httpx.HTTPError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            suffix = f" (HTTP {status})" if status else ""
            raise ProviderError(f"provider request failed{suffix}") from exc
        if not isinstance(body, dict):
            raise ProviderError("provider returned an invalid response")
        return body


class OpenAIChatProvider(_OpenAICompatibleProvider):
    endpoint = "chat/completions"

    def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.2, max_tokens: int | None = None) -> str:
        payload: dict = {"model": self.model, "messages": messages, "temperature": temperature}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        body = self._post(payload)
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("provider returned no chat content") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("provider returned empty chat content")
        return content.strip()

    def stream(self, messages: list[dict[str, str]], *, temperature: float = 0.2):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        try:
            with self.client.stream(
                "POST",
                f"{self.base_url}/{self.endpoint}",
                headers=self._headers(),
                json=payload,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        body = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise ProviderError("provider returned an invalid streaming response") from exc
                    if not isinstance(body, dict):
                        raise ProviderError("provider returned an invalid streaming response")
                    if body.get("error") is not None:
                        raise ProviderError("provider returned a streaming error")
                    choices = body.get("choices")
                    if choices == []:
                        continue
                    if not isinstance(choices, list) or not isinstance(choices[0], dict):
                        raise ProviderError("provider returned an invalid streaming response")
                    choice = choices[0]
                    delta = choice.get("delta")
                    if delta is None and choice.get("finish_reason") is not None:
                        continue
                    if not isinstance(delta, dict):
                        raise ProviderError("provider returned an invalid streaming response")
                    content = delta.get("content")
                    if content is not None and not isinstance(content, str):
                        raise ProviderError("provider returned an invalid streaming response")
                    if isinstance(content, str) and content:
                        yield content
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            suffix = f" (HTTP {status})" if status else ""
            raise ProviderError(f"provider streaming request failed{suffix}") from exc


class OpenAIEmbeddingProvider(_OpenAICompatibleProvider):
    endpoint = "embeddings"

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        body = self._post({"model": self.model, "input": texts})
        try:
            rows = sorted(body["data"], key=lambda item: item["index"])
            vectors = [row["embedding"] for row in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("provider returned invalid embeddings") from exc
        if len(vectors) != len(texts) or any(not isinstance(vector, list) for vector in vectors):
            raise ProviderError("provider returned an incomplete embedding batch")
        return vectors


def chat_provider(
    settings,
    route_key: str | None = None,
    timeout: float = PROVIDER_TIMEOUT_SECONDS,
) -> OpenAIChatProvider | None:
    if route_key:
        selected = _selected_profile(settings, route_key, "llm")
        if selected:
            return OpenAIChatProvider(*selected, timeout=timeout)
    if settings.llm_api_key and settings.llm_model and settings.llm_base_url:
        return OpenAIChatProvider(
            settings.llm_model,
            settings.llm_base_url,
            settings.llm_api_key,
            timeout=timeout,
        )
    return None


def embedding_provider(settings, route_key: str | None = None) -> OpenAIEmbeddingProvider | None:
    if route_key:
        selected = _selected_profile(settings, route_key, "embedding")
        if selected:
            return OpenAIEmbeddingProvider(*selected)
    if settings.embedding_api_key and settings.embedding_model and settings.embedding_base_url:
        return OpenAIEmbeddingProvider(settings.embedding_model, settings.embedding_base_url, settings.embedding_api_key)
    return None
