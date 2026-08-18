from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..config import Settings
from ..db import connect

AI_QUERY_PROPERTY_LIMIT_KEY = "ai_query_property_limit"
AI_QUERY_ENTITY_LIMIT_KEY = "ai_query_entity_limit"
AI_QUERY_TOTAL_NODE_LIMIT_KEY = "ai_query_total_node_limit"
SEARCH_PROPERTY_LIMIT_KEY = "search_property_limit"
SEARCH_ENTITY_LIMIT_KEY = "search_entity_limit"

MAX_RETRIEVAL_LIMIT_PER_KIND = 200
MAX_RETRIEVAL_TOTAL_NODE_LIMIT = 400


@dataclass(frozen=True)
class RetrievalLimits:
    ai_query_property_limit: int = 15
    ai_query_entity_limit: int = 15
    ai_query_total_node_limit: int = 30
    search_property_limit: int = 30
    search_entity_limit: int = 30

    def as_system_config(self) -> dict[str, dict[str, int]]:
        return {
            "ai_query": {
                "property_limit": self.ai_query_property_limit,
                "entity_limit": self.ai_query_entity_limit,
                "total_node_limit": self.ai_query_total_node_limit,
            },
            "search": {
                "property_limit": self.search_property_limit,
                "entity_limit": self.search_entity_limit,
            },
        }


DEFAULT_RETRIEVAL_LIMITS = RetrievalLimits()
RETRIEVAL_LIMIT_DEFAULTS = {
    AI_QUERY_PROPERTY_LIMIT_KEY: DEFAULT_RETRIEVAL_LIMITS.ai_query_property_limit,
    AI_QUERY_ENTITY_LIMIT_KEY: DEFAULT_RETRIEVAL_LIMITS.ai_query_entity_limit,
    AI_QUERY_TOTAL_NODE_LIMIT_KEY: DEFAULT_RETRIEVAL_LIMITS.ai_query_total_node_limit,
    SEARCH_PROPERTY_LIMIT_KEY: DEFAULT_RETRIEVAL_LIMITS.search_property_limit,
    SEARCH_ENTITY_LIMIT_KEY: DEFAULT_RETRIEVAL_LIMITS.search_entity_limit,
}


def _positive_int(value: object, default: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    if parsed < 1 or parsed > maximum:
        return default
    return parsed


def retrieval_limits_from_values(values: Mapping[str, object]) -> RetrievalLimits:
    return RetrievalLimits(
        ai_query_property_limit=_positive_int(
            values.get(AI_QUERY_PROPERTY_LIMIT_KEY),
            DEFAULT_RETRIEVAL_LIMITS.ai_query_property_limit,
            MAX_RETRIEVAL_LIMIT_PER_KIND,
        ),
        ai_query_entity_limit=_positive_int(
            values.get(AI_QUERY_ENTITY_LIMIT_KEY),
            DEFAULT_RETRIEVAL_LIMITS.ai_query_entity_limit,
            MAX_RETRIEVAL_LIMIT_PER_KIND,
        ),
        ai_query_total_node_limit=_positive_int(
            values.get(AI_QUERY_TOTAL_NODE_LIMIT_KEY),
            DEFAULT_RETRIEVAL_LIMITS.ai_query_total_node_limit,
            MAX_RETRIEVAL_TOTAL_NODE_LIMIT,
        ),
        search_property_limit=_positive_int(
            values.get(SEARCH_PROPERTY_LIMIT_KEY),
            DEFAULT_RETRIEVAL_LIMITS.search_property_limit,
            MAX_RETRIEVAL_LIMIT_PER_KIND,
        ),
        search_entity_limit=_positive_int(
            values.get(SEARCH_ENTITY_LIMIT_KEY),
            DEFAULT_RETRIEVAL_LIMITS.search_entity_limit,
            MAX_RETRIEVAL_LIMIT_PER_KIND,
        ),
    )


def load_retrieval_limits(settings: Settings) -> RetrievalLimits:
    keys = tuple(RETRIEVAL_LIMIT_DEFAULTS)
    placeholders = ",".join("?" for _ in keys)
    with connect(settings.sqlite_path) as db:
        values = {
            row["key"]: row["value"]
            for row in db.execute(
                f"SELECT key,value FROM system_config WHERE key IN ({placeholders})",
                keys,
            )
        }
    return retrieval_limits_from_values(values)
