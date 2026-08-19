from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from ..db import connect


def save_llm_invocation(
    sqlite_path: Path,
    *,
    request_time: str,
    response_time: str,
    duration_ms: int,
    model: str,
    route_key: str | None,
    profile_id: str | None,
    status: str,
    request_prompt: str,
    response_output: str,
    invocation_id: str | None = None,
) -> str | None:
    """Persist an LLM audit record without affecting the model request outcome."""
    invocation_id = invocation_id or str(uuid.uuid4())
    try:
        with connect(sqlite_path) as db:
            db.execute(
                """INSERT INTO llm_invocation_logs(
                       id,request_time,response_time,duration_ms,model,route_key,
                       profile_id,status,request_prompt,response_output
                   ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    invocation_id,
                    request_time,
                    response_time,
                    max(0, duration_ms),
                    model,
                    route_key,
                    profile_id,
                    status,
                    request_prompt,
                    response_output,
                ),
            )
    except (OSError, sqlite3.Error):
        # Observability must never turn a successful LLM call into a failed one.
        return None
    return invocation_id


class LoggedLLMResponse(str):
    """String response carrying the audit row that recorded its invocation."""

    sqlite_path: Path
    invocation_id: str

    def __new__(
        cls,
        value: str,
        *,
        sqlite_path: Path,
        invocation_id: str,
    ) -> LoggedLLMResponse:
        response = super().__new__(cls, value)
        response.sqlite_path = sqlite_path
        response.invocation_id = invocation_id
        return response


def logged_llm_response(
    value: str,
    *,
    sqlite_path: Path | None,
    invocation_id: str | None,
) -> str:
    if sqlite_path is None or invocation_id is None:
        return value
    return LoggedLLMResponse(
        value,
        sqlite_path=sqlite_path,
        invocation_id=invocation_id,
    )


def mark_llm_invocation_validation_failed(response: Any) -> None:
    """Relabel a completed provider call when its output fails caller validation."""
    sqlite_path = getattr(response, "sqlite_path", None)
    invocation_id = getattr(response, "invocation_id", None)
    if not isinstance(sqlite_path, Path) or not isinstance(invocation_id, str):
        return
    try:
        with connect(sqlite_path) as db:
            db.execute(
                "UPDATE llm_invocation_logs SET status='error' WHERE id=? AND status='success'",
                (invocation_id,),
            )
    except (OSError, sqlite3.Error):
        return
