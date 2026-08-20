import json
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..security import get_current_user, require_capability
from ..services.ai_query_tools import AIQueryTools
from ..services.catalog import PropertyCatalog
from ..services.display_language import current_display_language, iterate_in_display_language
from ..services.graph_store import Neo4jGraphStore
from ..services.llm import AnswerLLM
from ..services.providers import ProviderError
from ..services.query_history import (
    DEFAULT_AI_QUERY_HISTORY_COMPACTION_TOKEN_THRESHOLD,
    QueryHistoryStore,
    compacted_history_message,
    estimated_history_tokens,
    load_history_compaction_token_threshold,
)
from ..services.retrieval import GraphRetriever
from ..services.retrieval_limits import load_retrieval_limits
from .projects import get_project

router = APIRouter(prefix="/projects", tags=["query"])


class QueryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    history: list[QueryMessage] = Field(default_factory=list)


def ai_query_events(
    question: str,
    context: dict,
    answer_llm: AnswerLLM,
    databases: list[str],
    history: list[dict[str, str]] | None = None,
    on_complete: Callable[[str, list[dict]], None] | None = None,
):
    result = answer_llm.stream_answer(question, context, history=history)
    initial_citations = json.dumps(
        result["citations"], ensure_ascii=False, sort_keys=True
    )
    yield json.dumps(
        {
            "type": "sources",
            "citations": result["citations"],
            "retrieved": {
                "properties": len(context.get("properties", [])),
                "entities": len(context.get("entities", [])),
                "relations": len(context.get("relations", [])),
                "retrieval_paths": len(context.get("retrieval_paths", [])),
                "databases": databases,
            },
        }
    ) + "\n"
    answer_chunks = []
    try:
        for chunk in result["chunks"]:
            answer_chunks.append(chunk)
            yield json.dumps({"type": "delta", "content": chunk}) + "\n"
    except ProviderError as exc:
        yield json.dumps({"type": "error", "message": str(exc)}) + "\n"
        return
    if (
        json.dumps(result["citations"], ensure_ascii=False, sort_keys=True)
        != initial_citations
    ):
        yield json.dumps(
            {"type": "sources", "citations": result["citations"]}
        ) + "\n"
    if on_complete:
        on_complete("".join(answer_chunks), result["citations"])
    yield json.dumps({"type": "done"}) + "\n"


def _llm_history(messages: list[dict]) -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": message["content"]}
        for message in messages
        if message.get("role") in {"user", "assistant"}
        and isinstance(message.get("content"), str)
        and message["content"]
    ]


def _history_context(
    history_store: QueryHistoryStore,
    project_id: str,
    user_id: str,
    history: list[dict[str, str]],
    answer_llm: AnswerLLM,
    token_threshold: int = DEFAULT_AI_QUERY_HISTORY_COMPACTION_TOKEN_THRESHOLD,
) -> list[dict[str, str]]:
    cached_compaction = getattr(history_store, "cached_compaction", None)
    compacted_history = (
        cached_compaction(project_id, user_id, history)
        if callable(cached_compaction)
        else None
    )
    if compacted_history:
        message_count = compacted_history["message_count"]
        history_context = [
            compacted_history_message(compacted_history["summary"]),
            *history[message_count:],
        ]
    else:
        history_context = history

    if estimated_history_tokens(history_context) <= token_threshold:
        return history_context

    compactor = getattr(answer_llm, "compact_history", None)
    if getattr(answer_llm, "provider", object()) is None or not callable(compactor):
        return history_context
    compacted_summary = compactor(history_context)
    compacted_history = [compacted_history_message(compacted_summary)]
    save_compaction = getattr(history_store, "save_compaction", None)
    if callable(save_compaction):
        save_compaction(project_id, user_id, history, compacted_summary)
    return compacted_history


def _property_group_tree(toolbox: AIQueryTools) -> dict:
    tree_builder = getattr(toolbox, "property_group_tree", None)
    return tree_builder() if callable(tree_builder) else {}


@router.get("/{project_id}/ai-query/history")
def get_ai_query_history(
    project_id: str,
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("query.execute")),
):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"messages": QueryHistoryStore(settings).list(project_id, user["id"])}


@router.delete("/{project_id}/ai-query/history", status_code=status.HTTP_204_NO_CONTENT)
def clear_ai_query_history(
    project_id: str,
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("query.execute")),
):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    QueryHistoryStore(settings).clear(project_id, user["id"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{project_id}/search")
def search(project_id: str, payload: QueryRequest, settings: Settings = Depends(get_settings), user=Depends(get_current_user)):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    if not ({"search.properties", "search.entities"} & user["capabilities"]):
        raise HTTPException(status_code=403, detail="Missing search capability")
    store = Neo4jGraphStore(settings)
    allowed_kinds = set()
    if "search.properties" in user["capabilities"]:
        allowed_kinds.add("property")
    if "search.entities" in user["capabilities"]:
        allowed_kinds.add("entity")
    limits = load_retrieval_limits(settings)
    grouped = GraphRetriever(store).search(
        project_id,
        payload.query,
        allowed_kinds=allowed_kinds,
        property_limit=limits.search_property_limit,
        entity_limit=limits.search_entity_limit,
        total_limit=(
            limits.search_property_limit + limits.search_entity_limit
        ),
    )
    result = {}
    if "search.properties" in user["capabilities"]:
        result["properties"] = grouped["properties"]
    if "search.entities" in user["capabilities"]:
        result["entities"] = grouped["entities"]
    return result


@router.post("/{project_id}/search/properties")
def search_properties(project_id: str, payload: QueryRequest, settings: Settings = Depends(get_settings), user=Depends(require_capability("search.properties"))):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    limits = load_retrieval_limits(settings)
    return {"properties": GraphRetriever(Neo4jGraphStore(settings)).search_properties(project_id, payload.query, limit=limits.search_property_limit)}


@router.post("/{project_id}/search/entities")
def search_entities(project_id: str, payload: QueryRequest, settings: Settings = Depends(get_settings), user=Depends(require_capability("search.entities"))):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    limits = load_retrieval_limits(settings)
    return {"entities": GraphRetriever(Neo4jGraphStore(settings)).search_entities(project_id, payload.query, limit=limits.search_entity_limit)}


@router.post("/{project_id}/ai-query")
def ai_query(project_id: str, payload: QueryRequest, settings: Settings = Depends(get_settings), user=Depends(require_capability("query.execute"))):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    store = Neo4jGraphStore(settings)
    limits = load_retrieval_limits(settings)
    context = GraphRetriever(store).context(
        project_id,
        payload.query,
        property_limit=limits.ai_query_property_limit,
        entity_limit=limits.ai_query_entity_limit,
        total_limit=limits.ai_query_total_node_limit,
    )
    history_store = QueryHistoryStore(settings)
    saved_history = history_store.list(project_id, user["id"])
    client_history = [message.model_dump() for message in payload.history]
    history = _llm_history(saved_history or client_history)
    toolbox = AIQueryTools(
        project_id,
        store,
        PropertyCatalog(settings),
    )
    context = {**context, "property_group_tree": _property_group_tree(toolbox)}
    answer_llm = AnswerLLM(settings=settings, toolbox=toolbox)
    history = _history_context(
        history_store,
        project_id,
        user["id"],
        history,
        answer_llm,
        load_history_compaction_token_threshold(settings),
    )
    result = answer_llm.answer(
        payload.query, context, history=history
    )
    history_store.append_exchange(
        project_id,
        user["id"],
        payload.query,
        result["answer"],
        result["citations"],
        initial_history=client_history,
    )
    return {
        **result,
        "retrieved": {
            "properties": len(context["properties"]),
            "entities": len(context["entities"]),
            "relations": len(context["relations"]),
            "retrieval_paths": len(context["retrieval_paths"]),
            "databases": [
                settings.neo4j_property_database,
                settings.neo4j_entity_database,
            ],
        },
    }


@router.post("/{project_id}/ai-query/stream")
def ai_query_stream(project_id: str, payload: QueryRequest, settings: Settings = Depends(get_settings), user=Depends(require_capability("query.execute"))):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    limits = load_retrieval_limits(settings)
    store = Neo4jGraphStore(settings)
    context = GraphRetriever(store).context(
        project_id,
        payload.query,
        property_limit=limits.ai_query_property_limit,
        entity_limit=limits.ai_query_entity_limit,
        total_limit=limits.ai_query_total_node_limit,
    )
    history_store = QueryHistoryStore(settings)
    saved_history = history_store.list(project_id, user["id"])
    client_history = [message.model_dump() for message in payload.history]
    history = _llm_history(saved_history or client_history)
    toolbox = AIQueryTools(
        project_id,
        store,
        PropertyCatalog(settings),
    )
    context = {**context, "property_group_tree": _property_group_tree(toolbox)}
    answer_llm = AnswerLLM(settings=settings, toolbox=toolbox)
    history = _history_context(
        history_store,
        project_id,
        user["id"],
        history,
        answer_llm,
        load_history_compaction_token_threshold(settings),
    )
    events = ai_query_events(
        payload.query,
        context,
        answer_llm,
        [settings.neo4j_property_database, settings.neo4j_entity_database],
        history,
        on_complete=lambda answer, citations: history_store.append_exchange(
            project_id,
            user["id"],
            payload.query,
            answer,
            citations,
            initial_history=client_history,
        ),
    )
    return StreamingResponse(
        iterate_in_display_language(current_display_language(), events),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
