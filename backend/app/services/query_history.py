from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from threading import RLock

from ..config import Settings


_HISTORY_LOCK = RLock()


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

    def _read_unlocked(self, path: Path) -> list[dict]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []
        return self._messages(payload.get("messages") if isinstance(payload, dict) else None)

    @staticmethod
    def _write_unlocked(path: Path, messages: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {"version": 1, "messages": messages},
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
            messages = self._read_unlocked(path)
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
            self._write_unlocked(path, self._messages(messages))

    def clear(self, project_id: str, user_id: str) -> None:
        path = self._path(project_id, user_id)
        with _HISTORY_LOCK:
            path.unlink(missing_ok=True)
