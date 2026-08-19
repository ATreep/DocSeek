from __future__ import annotations

from typing import Mapping

from ..config import Settings
from ..db import connect

BATCH_LLM_CONCURRENCY_KEY = "batch_llm_concurrency"
MAX_BATCH_LLM_CONCURRENCY = 50


def _valid_concurrency(value: object, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        parsed = default
    if parsed < 1 or parsed > MAX_BATCH_LLM_CONCURRENCY:
        return max(1, min(MAX_BATCH_LLM_CONCURRENCY, default))
    return parsed


def batch_llm_concurrency_from_values(
    values: Mapping[str, object],
    default: int = 50,
) -> int:
    return _valid_concurrency(values.get(BATCH_LLM_CONCURRENCY_KEY), default)


def load_batch_llm_concurrency(settings: Settings) -> int:
    with connect(settings.sqlite_path) as db:
        row = db.execute(
            "SELECT value FROM system_config WHERE key=?",
            (BATCH_LLM_CONCURRENCY_KEY,),
        ).fetchone()
    values = {BATCH_LLM_CONCURRENCY_KEY: row["value"]} if row else {}
    return batch_llm_concurrency_from_values(
        values,
        default=int(settings.batch_llm_concurrency),
    )
