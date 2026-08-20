from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from threading import RLock
from typing import Mapping

from ..config import Settings
from ..db import connect


_HISTORY_LOCK = RLock()
AI_QUERY_HISTORY_COMPACTION_TOKEN_THRESHOLD_KEY = (
    "ai_query_history_compaction_token_threshold"
)
DEFAULT_AI_QUERY_HISTORY_COMPACTION_TOKEN_THRESHOLD = 150_000
_COMPACTED_HISTORY_PREFIX = "Compacted earlier conversation context:\n"


def history_compaction_token_threshold_from_values(
    values: Mapping[str, object],
) -> int:
    try:
        threshold = int(
            str(values.get(AI_QUERY_HISTORY_COMPACTION_TOKEN_THRESHOLD_KEY))
        )
    except (TypeError, ValueError):
        return DEFAULT_AI_QUERY_HISTORY_COMPACTION_TOKEN_THRESHOLD
    if threshold < 1:
        return DEFAULT_AI_QUERY_HISTORY_COMPACTION_TOKEN_THRESHOLD
    return threshold


def load_history_compaction_token_threshold(settings: Settings) -> int:
    sqlite_path = getattr(settings, "sqlite_path", None)
    if sqlite_path is None:
        return DEFAULT_AI_QUERY_HISTORY_COMPACTION_TOKEN_THRESHOLD
    with connect(sqlite_path) as db:
        row = db.execute(
            "SELECT value FROM system_config WHERE key=?",
            (AI_QUERY_HISTORY_COMPACTION_TOKEN_THRESHOLD_KEY,),
        ).fetchone()
    return history_compaction_token_threshold_from_values(
        {
            AI_QUERY_HISTORY_COMPACTION_TOKEN_THRESHOLD_KEY: (
                row["value"] if row else None
            )
        }
    )


def _estimated_text_tokens(value: str) -> int:
    byte_estimate = (len(value.encode("utf-8")) + 3) // 4
    lexical_estimate = len(
        re.findall(r"[A-Za-z0-9_]+|[^\x00-\x7F\s]|[^\w\s]", value)
    )
    return max(byte_estimate, lexical_estimate)


def estimated_history_tokens(messages: list[dict[str, str]]) -> int:
    return sum(
        4 + _estimated_text_tokens(str(message.get("content") or ""))
        for message in messages
    )


def compacted_history_message(summary: str) -> dict[str, str]:
    return {
        "role": "assistant",
        "content": f"{_COMPACTED_HISTORY_PREFIX}{summary.strip()}",
    }


class QueryHistoryStore:
    def __init__(self, settings: Settings):
        self.projects_dir = settings.projects_dir

    @staticmethod
    def _safe_project_id(project_id: str) -> str:
        if not project_id or Path(project_id).name != project_id:
            raise ValueError("Invalid project ID")
        return project_id

    def _path(self, project_id: str, user_id: str) -> Path:
        project_id = self._safe_project_id(project_id)
        if not user_id:
            raise ValueError("User ID is required")
        user_key = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        return self.projects_dir / project_id / "query-history" / f"{user_key}.json"

    @staticmethod
    def _citations(value: object) -> list[dict]:
        if not isinstance(value, list):
            return []
        citations = []
        for item in value:
            if not isinstance(item, dict):
                continue
            citation = {
                key: item[key]
                for key in ("kind", "id", "label", "reason")
                if isinstance(item.get(key), str) and item[key]
            }
            path = item.get("path")
            if (
                isinstance(path, list)
                and path
                and all(isinstance(part, str) and part for part in path)
            ):
                citation["path"] = path
            if citation:
                citations.append(citation)
        return citations

    @classmethod
    def _messages(cls, value: object) -> list[dict]:
        if not isinstance(value, list):
            return []
        messages = []
        for item in value:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
                continue
            message = {"role": role, "content": content}
            citations = cls._citations(item.get("citations"))
            if role == "assistant" and citations:
                message["citations"] = citations
            messages.append(message)
        return messages

    @classmethod
    def _content_messages(cls, value: object) -> list[dict[str, str]]:
        return [
            {"role": message["role"], "content": message["content"]}
            for message in cls._messages(value)
        ]

    @staticmethod
    def _compaction(value: object, messages: list[dict]) -> dict | None:
        if not isinstance(value, dict):
            return None
        summary = value.get("summary")
        message_count = value.get("message_count")
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or not isinstance(message_count, int)
            or isinstance(message_count, bool)
            or message_count < 1
            or message_count > len(messages)
        ):
            return None
        return {"summary": summary.strip(), "message_count": message_count}

    def _read_payload_unlocked(self, path: Path) -> tuple[list[dict], dict | None]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return [], None
        messages = self._messages(
            payload.get("messages") if isinstance(payload, dict) else None
        )
        compaction = self._compaction(
            payload.get("compaction") if isinstance(payload, dict) else None,
            messages,
        )
        return messages, compaction

    def _read_unlocked(self, path: Path) -> list[dict]:
        return self._read_payload_unlocked(path)[0]

    @staticmethod
    def _write_unlocked(
        path: Path,
        messages: list[dict],
        compaction: dict | None = None,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                payload = {"version": 2, "messages": messages}
                if compaction:
                    payload["compaction"] = compaction
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def list(self, project_id: str, user_id: str) -> list[dict]:
        path = self._path(project_id, user_id)
        with _HISTORY_LOCK:
            return self._read_unlocked(path)

    def cached_compaction(
        self,
        project_id: str,
        user_id: str,
        messages: list[dict],
    ) -> dict | None:
        path = self._path(project_id, user_id)
        normalized_messages = self._content_messages(messages)
        with _HISTORY_LOCK:
            saved_messages, compaction = self._read_payload_unlocked(path)
        if self._content_messages(saved_messages) != normalized_messages:
            return None
        return dict(compaction) if compaction else None

    def save_compaction(
        self,
        project_id: str,
        user_id: str,
        messages: list[dict],
        summary: str,
    ) -> None:
        normalized_messages = self._content_messages(messages)
        normalized_summary = summary.strip()
        if not normalized_messages or not normalized_summary:
            return
        path = self._path(project_id, user_id)
        with _HISTORY_LOCK:
            saved_messages, _ = self._read_payload_unlocked(path)
            if not saved_messages:
                saved_messages = normalized_messages
            elif (
                self._content_messages(saved_messages)[: len(normalized_messages)]
                != normalized_messages
            ):
                return
            self._write_unlocked(
                path,
                saved_messages,
                {
                    "message_count": len(normalized_messages),
                    "summary": normalized_summary,
                },
            )

    def append_exchange(
        self,
        project_id: str,
        user_id: str,
        question: str,
        answer: str,
        citations: list[dict],
        initial_history: list[dict] | None = None,
    ) -> None:
        if not question.strip() or not answer.strip():
            return
        path = self._path(project_id, user_id)
        with _HISTORY_LOCK:
            messages, compaction = self._read_payload_unlocked(path)
            if not messages and initial_history:
                messages = self._messages(initial_history)
            messages.extend(
                [
                    {"role": "user", "content": question},
                    {
                        "role": "assistant",
                        "content": answer,
                        "citations": self._citations(citations),
                    },
                ]
            )
            self._write_unlocked(path, self._messages(messages), compaction)

    def clear(self, project_id: str, user_id: str) -> None:
        path = self._path(project_id, user_id)
        with _HISTORY_LOCK:
            path.unlink(missing_ok=True)
