from __future__ import annotations

import json
import base64
import mimetypes
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Semaphore, Thread
from typing import Callable, Sequence, TypeVar, TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import Settings
from ..db import connect
from ..api.projects import release_lock
from .agents import DGAgent, GAAgent
from .catalog import PropertyCatalog
from .extraction_text import (
    DEFAULT_EXTRACTION_TEXT_MAX_CHARS,
    ExtractionSelection,
    TemporaryExtractionStore,
    select_extraction_text,
)
from .display_language import current_display_language, run_in_display_language
from .grouping import apply_group_placements
from .model_errors import extract_model_response
from .parallelism import load_batch_llm_concurrency
from .graph_store import (
    DEFAULT_ENTITY_PROMPT,
    DEFAULT_ENTITY_SCHEMA,
    ENTITY_CONTEXT_WORD_LIMIT,
    GraphRAGBuilder,
    GraphSnapshot,
    Neo4jGraphStore,
    _context_word_count,
    embeddings_for_texts,
    entity_embedding_text,
    build_property_group_graph,
    prune_property_snapshot,
    prune_properties_snapshot,
)
from .providers import chat_provider, embedding_provider, provider_route_metadata
from .relation_batches import (
    CollectionPair,
    apply_entity_merges,
    consolidate_exact_entity_ids,
    deduplicate_edges,
    merge_call_specs,
    relation_call_specs,
)
from .parsers import extract_text
from .storage import delete_property_text, move_original, read_property_text, safe_directory, write_property_text


class PipelineState(TypedDict, total=False):
    settings: Settings
    project_id: str
    job_id: str
    property_id: str
    filename: str
    kind: str
    path: str
    text: str
    comment: str
    definition: str
    directory: str
    properties: list[dict]
    documents: list[dict]
    entities: list[dict]
    entity_edges: list[dict]
    property_edges: list[dict]
    snapshot_id: str
    operation: str
    definition_override: str
    batch_items: list[dict]
    directories: dict[str, str]
    resume_snapshot_id: str
    completed_property_ids: list[str]
    extraction_path: str
    extraction_text: str
    extraction: dict


class JobCancelled(Exception):
    pass


JOB_HEARTBEAT_SECONDS = 10.0
BatchResult = TypeVar("BatchResult")


def _batch_llm_workers(settings: Settings, total: int) -> int:
    return max(1, min(total, load_batch_llm_concurrency(settings)))


def _run_bounded_calls(
    settings: Settings,
    job_id: str,
    calls: Sequence[CollectionPair],
    worker: Callable[[CollectionPair], BatchResult],
    *,
    thread_name_prefix: str,
    on_complete: Callable[[int, int], None] | None = None,
) -> list[BatchResult]:
    if not calls:
        return []
    output_language = current_display_language()
    results: dict[int, BatchResult] = {}
    with ThreadPoolExecutor(
        max_workers=_batch_llm_workers(settings, len(calls)),
        thread_name_prefix=thread_name_prefix,
    ) as executor:
        futures = {
            executor.submit(
                run_in_display_language,
                output_language,
                worker,
                call,
            ): index
            for index, call in enumerate(calls)
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            _raise_if_cancelled(settings, job_id)
            results[futures[future]] = future.result()
            if on_complete is not None:
                on_complete(completed, len(calls))
    return [results[index] for index in range(len(calls))]


def _flatten_call_results(
    results: Sequence[Sequence[dict]],
) -> list[dict]:
    return [item for result in results for item in result]


def _entity_relation_evidence(
    entity: dict,
    filenames_by_property_id: dict[str, str],
) -> dict:
    return {
        **entity,
        "source_contexts": [
            {
                **context,
                "property_filename": filenames_by_property_id.get(
                    str(context.get("property_id") or ""),
                    str(context.get("property_id") or ""),
                ),
            }
            for context in entity.get("source_contexts") or []
            if isinstance(context, dict)
        ],
    }


def _job_timings(raw: str | None) -> dict[str, float]:
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key): float(value)
        for key, value in parsed.items()
        if isinstance(value, (int, float))
    }


def _json_object(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _merge_job_progress(settings: Settings, job_id: str, **changes) -> dict:
    with connect(settings.sqlite_path) as db:
        row = db.execute(
            "SELECT progress_json FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        progress = _json_object(row["progress_json"] if row else None)
        progress.update(changes)
        db.execute(
            "UPDATE jobs SET progress_json=?,heartbeat=? WHERE id=?",
            (
                json.dumps(progress, ensure_ascii=False, separators=(",", ":")),
                datetime.now(timezone.utc).isoformat(),
                job_id,
            ),
        )
    return progress


def _transition_job(
    settings: Settings,
    job_id: str,
    stage: str,
    status: str = "running",
    detail: str = "",
    **extra,
) -> None:
    now = datetime.now(timezone.utc)
    with connect(settings.sqlite_path) as db:
        current = db.execute(
            "SELECT stage,stage_started_at,timings_json FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        timings = _job_timings(current["timings_json"] if current else None)
        same_stage = bool(current and current["stage"] == stage)
        if (
            current
            and not same_stage
            and current["stage"]
            and current["stage_started_at"]
        ):
            try:
                started = datetime.fromisoformat(current["stage_started_at"])
            except ValueError:
                started = None
            if started is not None:
                elapsed = max(0.0, (now - started).total_seconds())
                timings[current["stage"]] = round(
                    timings.get(current["stage"], 0.0) + elapsed, 3
                )
        stage_started_at = (
            current["stage_started_at"]
            if same_stage and current["stage_started_at"]
            else now.isoformat()
        )
        fields = {
            "stage": stage,
            "status": status,
            "heartbeat": now.isoformat(),
            "stage_started_at": stage_started_at,
            "stage_detail": detail,
            "timings_json": json.dumps(timings, separators=(",", ":")),
            **extra,
        }
        assignments = ", ".join(f"{key}=?" for key in fields)
        db.execute(f"UPDATE jobs SET {assignments} WHERE id=?", (*fields.values(), job_id))


def _update_job(settings: Settings, job_id: str, **fields) -> None:
    if not fields:
        return
    fields = {**fields, "heartbeat": datetime.now(timezone.utc).isoformat()}
    assignments = ", ".join(f"{key}=?" for key in fields)
    with connect(settings.sqlite_path) as db:
        db.execute(f"UPDATE jobs SET {assignments} WHERE id=?", (*fields.values(), job_id))


@contextmanager
def _job_heartbeat(settings: Settings, job_id: str):
    stop = Event()

    def keep_alive() -> None:
        while not stop.wait(JOB_HEARTBEAT_SECONDS):
            with connect(settings.sqlite_path) as db:
                db.execute(
                    "UPDATE jobs SET heartbeat=? WHERE id=? AND status='running'",
                    (datetime.now(timezone.utc).isoformat(), job_id),
                )

    worker = Thread(target=keep_alive, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=max(1.0, JOB_HEARTBEAT_SECONDS * 2))


def _raise_if_cancelled(settings: Settings, job_id: str) -> None:
    with connect(settings.sqlite_path) as db:
        row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row and row["status"] == "cancelled":
        raise JobCancelled(job_id)


def _directory_from_relative_path(relative_path: str) -> str:
    path = Path(relative_path)
    if path.parts[:1] != ("properties",) or path.parent == Path("properties"):
        return ""
    return str(path.parent.relative_to(Path("properties")))


def _relative_path(directory: str, filename: str) -> str:
    return str(Path("properties") / safe_directory(directory) / filename)


def _property_content(
    settings: Settings,
    project_id: str,
    property_id: str,
    path: Path,
    kind: str,
) -> str:
    persisted = read_property_text(settings, project_id, property_id)
    return persisted if persisted is not None else extract_text(path, kind)


def _prepare_entity_document(
    document: dict,
    existing_entities: list[dict],
    *,
    max_chars: int = DEFAULT_EXTRACTION_TEXT_MAX_CHARS,
) -> tuple[dict, ExtractionSelection]:
    full_text = str(document.get("original_text") or document.get("text") or "")
    selection = select_extraction_text(
        full_text,
        filename=str(document.get("filename") or ""),
        definition=str(document.get("definition") or ""),
        import_context=str(document.get("import_context") or ""),
        existing_entities=existing_entities,
        max_chars=max_chars,
    )
    return (
        {
            **document,
            "text": selection.text,
            "original_text": full_text,
            "extraction_chunks": [
                chunk.to_dict() for chunk in selection.chunks
            ],
        },
        selection,
    )


def _current_group_tree(
    catalog_rows: list[dict], excluded_property_id: str | None = None
) -> dict:
    root = {
        "group_name": "",
        "group_path": "",
        "properties": [],
        "groups": [],
    }
    nodes = {"": root}
    for row in sorted(
        catalog_rows,
        key=lambda item: (
            str(item.get("directory") or item.get("relative_path") or "").casefold(),
            str(item.get("filename") or "").casefold(),
        ),
    ):
        if row.get("id") == excluded_property_id or not row.get("filename"):
            continue
        group_path = row.get("directory") or _directory_from_relative_path(
            row.get("relative_path") or ""
        )
        parent = root
        path_parts = [part for part in Path(group_path).parts if part not in {"", "."}]
        for index, group_name in enumerate(path_parts):
            current_path = "/".join(path_parts[: index + 1])
            if current_path not in nodes:
                node = {
                    "group_name": group_name,
                    "group_path": current_path,
                    "properties": [],
                    "groups": [],
                }
                parent["groups"].append(node)
                nodes[current_path] = node
            parent = nodes[current_path]
        parent["properties"].append(
            {
                "property_id": row.get("id") or "",
                "filename": row["filename"],
                "property_type": row.get("property_type") or "",
                "definition": row.get("definition") or "",
            }
        )
    return root


def _should_extract_entities_incrementally(
    operation: str | None, property_id: str | None, active_properties: dict
) -> bool:
    return (
        operation in {"add", "retry"}
        and property_id is not None
        and property_id not in active_properties
    )


def _merge_entity_contexts(
    current: list[dict], incoming: list[dict]
) -> list[dict]:
    merged = []
    seen = set()
    used_words = 0
    for context in [*(current or []), *(incoming or [])]:
        if not isinstance(context, dict):
            continue
        property_id = str(context.get("property_id") or "")
        text = str(context.get("text") or "").strip()
        key = (property_id, text)
        if not property_id or not text or key in seen:
            continue
        word_count = _context_word_count(text)
        if used_words + word_count > ENTITY_CONTEXT_WORD_LIMIT:
            continue
        merged.append({"property_id": property_id, "text": text})
        seen.add(key)
        used_words += word_count
    return merged


def _merge_entity_delta(
    current_nodes: list[dict],
    current_edges: list[dict],
    delta_nodes: list[dict],
    delta_edges: list[dict],
) -> tuple[list[dict], list[dict]]:
    nodes_by_id = {
        str(node["id"]): dict(node) for node in current_nodes if node.get("id")
    }
    node_order = list(nodes_by_id)
    for delta in delta_nodes:
        entity_id = str(delta.get("id") or "")
        if not entity_id:
            continue
        current = nodes_by_id.get(entity_id, {})
        merged = {**current, **delta, "id": entity_id}
        for field in ("name", "definition", "project_id"):
            if not delta.get(field) and current.get(field):
                merged[field] = current[field]
        merged["source_property_ids"] = list(
            dict.fromkeys(
                [
                    *(current.get("source_property_ids") or []),
                    *(delta.get("source_property_ids") or []),
                ]
            )
        )
        merged["source_contexts"] = _merge_entity_contexts(
            current.get("source_contexts") or [],
            delta.get("source_contexts") or [],
        )
        nodes_by_id[entity_id] = merged
        if entity_id not in node_order:
            node_order.append(entity_id)

    edges_by_endpoints = {
        (str(edge.get("source")), str(edge.get("target"))): dict(edge)
        for edge in current_edges
        if edge.get("source") and edge.get("target")
    }
    edge_order = list(edges_by_endpoints)
    for edge in delta_edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if not source or not target:
            continue
        key = (source, target)
        edges_by_endpoints[key] = {**edge, "source": source, "target": target}
        if key not in edge_order:
            edge_order.append(key)
    return (
        [nodes_by_id[entity_id] for entity_id in node_order],
        [edges_by_endpoints[key] for key in edge_order],
    )


def build_workflow(graph_store: Neo4jGraphStore):
    builder = StateGraph(PipelineState)

    def dg(state: PipelineState):
        _raise_if_cancelled(state["settings"], state["job_id"])
        _transition_job(
            state["settings"],
            state["job_id"],
            "dg-agent",
            detail=f"Analyzing {state['filename']}",
        )
        if state.get("operation") == "remove":
            return {"definition": ""}
        if state.get("operation") == "batch-add":
            return {}
        if state.get("operation") == "metadata":
            return {"definition": state.get("definition_override", "")}
        if state.get("operation") == "add" and state.get("definition_override"):
            return {"definition": state["definition_override"]}
        image_data_url = None
        if state["kind"] == "image":
            media_type = mimetypes.guess_type(state["filename"])[0] or "application/octet-stream"
            image_data_url = (
                f"data:{media_type};base64,"
                f"{base64.b64encode(Path(state['path']).read_bytes()).decode('ascii')}"
            )
        result = DGAgent(settings=state["settings"]).generate(
            state["filename"],
            state["kind"],
            state.get("text", ""),
            state.get("comment", ""),
            image_data_url=image_data_url,
            extraction_text=state.get("extraction_text"),
        )
        return {"definition": result.definition, "text": result.content}

    def ga(state: PipelineState):
        _raise_if_cancelled(state["settings"], state["job_id"])
        _transition_job(
            state["settings"],
            state["job_id"],
            "ga-agent",
            detail="Choosing a property group",
        )
        if state.get("operation") == "remove":
            return {"directory": ""}
        catalog_rows = PropertyCatalog(state["settings"]).list(state["project_id"])
        if state.get("operation") == "batch-add":
            batch_items = state.get("batch_items") or []
            if state.get("directories"):
                return {"directories": state["directories"]}
            tree_context = _current_group_tree(catalog_rows)
            directories = GAAgent(settings=state["settings"]).organize_tree(
                tree_context,
                {
                    item["property_id"]: item.get("comment", "")
                    for item in batch_items
                },
            )
            _merge_job_progress(
                state["settings"], state["job_id"], directories=directories
            )
            return {"directories": directories}
        if state.get("operation") == "metadata":
            current = next(
                row for row in catalog_rows if row["id"] == state["property_id"]
            )
            return {
                "directory": current.get("directory")
                or _directory_from_relative_path(current["relative_path"])
            }
        tree_context = _current_group_tree(
            catalog_rows, state["property_id"]
        )
        directory = GAAgent(settings=state["settings"]).suggest_path(
            state["definition"],
            tree_context,
            filename=state["filename"],
            property_type=state["kind"],
            user_context=state.get("comment", ""),
        )
        return {"directory": directory}

    def graphs(state: PipelineState):
        _raise_if_cancelled(state["settings"], state["job_id"])
        settings, project_id = state["settings"], state["project_id"]
        catalog = PropertyCatalog(settings)
        rows = catalog.list(project_id)
        batch_items = state.get("batch_items") or []
        batch_by_id = {
            item["property_id"]: item for item in batch_items if item.get("property_id")
        }
        batch_property_ids = list(batch_by_id)
        is_batch_add = state.get("operation") == "batch-add"
        remaining_rows = [
            row
            for row in rows
            if not (
                row["id"] == state.get("property_id")
                and state.get("operation") == "remove"
            )
        ]
        _transition_job(
            settings,
            state["job_id"],
            "graph-property-read",
            detail=f"Preparing {len(remaining_rows)} properties",
        )
        resume_snapshot_id = state.get("resume_snapshot_id")
        active_property_graph = graph_store.graph(
            project_id, "property", resume_snapshot_id
        ) or {}
        active_properties = {
            item["id"]: item
            for item in active_property_graph.get("nodes", [])
            if item.get("id") and item.get("node_type") != "group"
        }
        incremental_entity_add = _should_extract_entities_incrementally(
            state.get("operation"), state.get("property_id"), active_properties
        ) or is_batch_add
        embedding_route_signature = json.dumps(
            provider_route_metadata(settings).get("shared_embedding_route", {}),
            sort_keys=True,
            separators=(",", ":"),
        )
        properties = []
        documents = []
        pending_embedding_indexes = []
        pending_embedding_texts = []
        for row in remaining_rows:
            path = settings.projects_dir / project_id / row["relative_path"]
            batch_item = batch_by_id.get(row["id"])
            is_processed_property = bool(batch_item) or row["id"] == state.get(
                "property_id"
            )
            active_property = active_properties.get(row["id"])
            if batch_item:
                text = batch_item.get("text", "")
            elif is_processed_property:
                text = state.get("text", "")
            elif active_property is not None and isinstance(active_property.get("content"), str):
                text = active_property["content"]
            else:
                text = extract_text(path, row["property_type"])
            if batch_item:
                definition = batch_item.get("definition", "")
            elif is_processed_property:
                definition = state["definition"]
            else:
                definition = row.get("definition") or ""
            if is_batch_add:
                directory = (state.get("directories") or {}).get(
                    row["id"], _directory_from_relative_path(row["relative_path"])
                )
                relative_path = _relative_path(directory, row["filename"])
            elif is_processed_property:
                directory = state.get("directory", "")
                relative_path = _relative_path(directory, row["filename"])
            else:
                directory = _directory_from_relative_path(row["relative_path"])
                relative_path = row["relative_path"]
            embedding_text = text or f"{definition} {row['filename']}"
            property_node = {
                "id": row["id"],
                "project_id": project_id,
                "filename": row["filename"],
                "property_type": row["property_type"],
                "definition": definition,
                "content": text,
                "relative_path": relative_path,
                "directory": directory,
                "node_type": "property",
                "_embedding_route_signature": embedding_route_signature,
            }
            active_embedding_text = ""
            if active_property is not None:
                active_content = str(active_property.get("content") or "")
                active_embedding_text = active_content or (
                    f"{active_property.get('definition') or ''} {active_property.get('filename') or ''}"
                )
            if (
                active_property is not None
                and active_property.get("embedding")
                and active_property.get("_embedding_route_signature")
                == embedding_route_signature
                and active_embedding_text == embedding_text
            ):
                property_node["embedding"] = active_property["embedding"]
            else:
                pending_embedding_indexes.append(len(properties))
                pending_embedding_texts.append(embedding_text)
            properties.append(property_node)
            if text:
                documents.append(
                    {
                        "project_id": project_id,
                        "property_id": row["id"],
                        "property_type": row["property_type"],
                        "filename": row["filename"],
                        "definition": definition,
                        "import_context": (
                            str(batch_item.get("comment") or "")
                            if batch_item
                            else str(state.get("comment") or "")
                            if is_processed_property
                            else ""
                        ),
                        "text": text,
                        "original_text": text,
                    }
                )
        with connect(settings.sqlite_path) as db:
            config = {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM system_config WHERE key IN ('entity_schema','entity_prompt')")}
        # A retry reads the candidate checkpoint so completed entity extraction
        # is preserved, but relation generation must compare those entities with
        # the graph that was active before this batch started.  Using the
        # checkpoint as both graphs makes every recovered entity look "old" and
        # therefore produces no relation-generation calls.
        current_entity_graph = graph_store.graph(
            project_id, "entity", resume_snapshot_id
        ) or {}
        current_entities = current_entity_graph.get("nodes", [])
        current_entity_edges = current_entity_graph.get("edges", [])
        if is_batch_add and resume_snapshot_id:
            baseline_entity_graph = graph_store.graph(project_id, "entity") or {}
        else:
            baseline_entity_graph = current_entity_graph
        baseline_entities = baseline_entity_graph.get("nodes", [])
        baseline_entity_edges = baseline_entity_graph.get("edges", [])
        checkpoint_resume = bool(is_batch_add and resume_snapshot_id)
        baseline_entities_by_id = {
            str(entity.get("id") or ""): entity
            for entity in baseline_entities
            if entity.get("id")
        }
        recovered_generated_entities = []
        for entity in current_entities:
            entity_id = str(entity.get("id") or "")
            if not entity_id:
                continue
            baseline_entity = baseline_entities_by_id.get(entity_id)
            if baseline_entity is None or (
                checkpoint_resume
                and (
                    entity.get("source_property_ids")
                    != baseline_entity.get("source_property_ids")
                    or entity.get("source_contexts")
                    != baseline_entity.get("source_contexts")
                )
            ):
                # Same-ID extraction results are included as a recovered delta
                # so exact-ID consolidation retains their new source metadata.
                recovered_generated_entities.append(dict(entity))
        documents_by_property_id = {
            document["property_id"]: document for document in documents
        }
        entity_documents = (
            [
                documents_by_property_id[property_id]
                for property_id in batch_property_ids
                if property_id in documents_by_property_id
            ]
            if is_batch_add
            else
            [
                document
                for document in documents
                if document["property_id"] == state["property_id"]
            ]
            if incremental_entity_add
            else documents
        )
        completed_property_ids = set(state.get("completed_property_ids") or [])
        if is_batch_add and completed_property_ids:
            entity_documents = [
                document
                for document in entity_documents
                if document["property_id"] not in completed_property_ids
            ]
        entity_generation_total = (
            len(batch_property_ids) if is_batch_add else len(entity_documents)
        )
        embedder = embedding_provider(settings, route_key="shared_embedding_route")
        output_language = current_display_language()
        try:
            _transition_job(
                settings,
                state["job_id"],
                "graph-property-embedding",
                detail=f"Updating {len(pending_embedding_texts)} changed property vectors",
            )
            if pending_embedding_texts:
                vectors = embeddings_for_texts(pending_embedding_texts, embedder)
                for index, vector in zip(pending_embedding_indexes, vectors):
                    properties[index]["embedding"] = vector
            property_groups, property_edges = build_property_group_graph(
                project_id,
                properties,
            )
            progress_lock = Lock()
            shared_llm_slots = Semaphore(load_batch_llm_concurrency(settings))
            entity_generation_progress = len(completed_property_ids)

            def update_entity_generation_progress(entities: int | None = None) -> None:
                nonlocal entity_generation_progress
                with progress_lock:
                    if entities is not None:
                        entity_generation_progress = entities
                    percent = (
                        min(
                            100,
                            max(
                                0,
                                int(
                                    entity_generation_progress
                                    * 100
                                    / entity_generation_total
                                ),
                            ),
                        )
                        if entity_generation_total
                        else 100
                    )
                    _transition_job(
                        settings,
                        state["job_id"],
                        "graph-entity-generation",
                        detail=f"Generating entity nodes: {percent}%",
                    )

            update_entity_generation_progress()
            try:
                generated_entities: list[dict] = list(recovered_generated_entities)
                entities = list(current_entities) if checkpoint_resume else (
                    list(baseline_entities) if incremental_entity_add else []
                )
                entity_edges = list(current_entity_edges) if checkpoint_resume else list(
                    baseline_entity_edges
                )
                checkpoint_snapshot_id = None
                total_batch_items = entity_generation_total
                filenames_by_property_id = {
                    str(property_node["id"]): str(
                        property_node.get("filename") or property_node["id"]
                    )
                    for property_node in properties
                }
                if is_batch_add:
                    checkpoint_snapshot_id = (
                        resume_snapshot_id or f"{state['job_id']}-checkpoint"
                    )
                    filenames_by_property_id.update(
                        {
                            item["property_id"]: item["filename"]
                            for item in batch_items
                        }
                    )

                # Entity extraction receives the original property document. Do not
                # run the relevance/section selection pass here: GraphRAGBuilder
                # retains its own 12,000-character overlapping chunk mechanism for
                # documents that exceed the model context budget.
                entity_items = []
                for index, document in enumerate(entity_documents):
                    _raise_if_cancelled(settings, state["job_id"])
                    entity_items.append(
                        {
                            "index": index,
                            "document": document,
                            "property_id": document["property_id"],
                            "filename": filenames_by_property_id.get(
                                document["property_id"],
                                document["property_id"],
                            ),
                        }
                    )

                def extract_document(entity_document):
                    with shared_llm_slots:
                        worker_llm = chat_provider(
                            settings,
                            route_key="entity_agent_route",
                            timeout=settings.entity_agent_timeout_seconds,
                        )
                        worker_builder = GraphRAGBuilder(
                            config.get("entity_schema", DEFAULT_ENTITY_SCHEMA),
                            config.get("entity_prompt", DEFAULT_ENTITY_PROMPT),
                            settings.neo4j_entity_database,
                            llm=worker_llm,
                        )
                        try:
                            result = worker_builder.build(
                                [entity_document],
                                embedder=None,
                            )
                            if isinstance(result, tuple) and len(result) == 2:
                                return result[0]
                            return result
                        finally:
                            if worker_llm is not None:
                                close = getattr(worker_llm, "close", None)
                                if close:
                                    close()

                if entity_items:
                    results_by_index = {}
                    first_error = None
                    with _job_heartbeat(settings, state["job_id"]):
                        with ThreadPoolExecutor(
                            max_workers=_batch_llm_workers(
                                settings, len(entity_items)
                            ),
                            thread_name_prefix="entity-generation",
                        ) as executor:
                            futures = {
                                executor.submit(
                                    run_in_display_language,
                                    output_language,
                                    extract_document,
                                    item["document"],
                                ): item
                                for item in entity_items
                            }
                            for future in as_completed(futures):
                                item = futures[future]
                                try:
                                    results_by_index[item["index"]] = future.result()
                                except Exception as exc:
                                    if first_error is None:
                                        first_error = exc
                                    continue
                                for result_index in sorted(results_by_index):
                                    delta_entities = results_by_index[result_index]
                                    generated_entities, _ = _merge_entity_delta(
                                        generated_entities,
                                        [],
                                        delta_entities,
                                        [],
                                    )
                                entities, _ = _merge_entity_delta(
                                    baseline_entities if incremental_entity_add else [],
                                    [],
                                    generated_entities,
                                    [],
                                )
                                entity_edges = baseline_entity_edges
                                completed_property_ids.add(item["property_id"])
                                update_entity_generation_progress(
                                    len(completed_property_ids)
                                )
                                if is_batch_add and checkpoint_snapshot_id:
                                    graph_store.write_snapshot(
                                        GraphSnapshot(
                                            project_id,
                                            checkpoint_snapshot_id,
                                            properties,
                                            entities,
                                            property_edges,
                                            entity_edges,
                                            property_groups,
                                        )
                                    )
                                    _update_job(
                                        settings,
                                        state["job_id"],
                                        candidate_snapshot=checkpoint_snapshot_id,
                                    )
                                    _merge_job_progress(
                                        settings,
                                        state["job_id"],
                                        completed_property_ids=sorted(
                                            completed_property_ids
                                        ),
                                        directories=state.get("directories") or {},
                                        candidate_snapshot=checkpoint_snapshot_id,
                                    )
                    if first_error is not None:
                        raise first_error
                elif incremental_entity_add:
                    entities = list(current_entities) if checkpoint_resume else list(
                        baseline_entities
                    )

                old_entities_for_relations = (
                    baseline_entities if incremental_entity_add else []
                )

                # Keep collection membership deterministic even when extraction
                # futures complete in a different order.  The batch property
                # order is the user-visible order and is also what checkpoint
                # recovery uses to reconstruct the new-entity set.
                property_order = {
                    str(property_id): index
                    for index, property_id in enumerate(batch_property_ids)
                }

                def entity_order_key(entity: dict) -> tuple[int, str]:
                    source_ids = [
                        str(source_id)
                        for source_id in entity.get("source_property_ids") or []
                    ]
                    source_index = min(
                        (
                            property_order.get(source_id, len(property_order))
                            for source_id in source_ids
                        ),
                        default=len(property_order),
                    )
                    return source_index, str(entity.get("id") or "")

                generated_entities.sort(key=entity_order_key)

                def entity_worker(pair: CollectionPair, method_name: str):
                    worker_llm = chat_provider(
                        settings,
                        route_key="entity_agent_route",
                        timeout=settings.entity_agent_timeout_seconds,
                    )
                    worker_builder = GraphRAGBuilder(
                        config.get("entity_schema", DEFAULT_ENTITY_SCHEMA),
                        config.get("entity_prompt", DEFAULT_ENTITY_PROMPT),
                        settings.neo4j_entity_database,
                        llm=worker_llm,
                    )
                    try:
                        return getattr(worker_builder, method_name)(pair)
                    finally:
                        if worker_llm is not None:
                            close = getattr(worker_llm, "close", None)
                            if close:
                                close()

                exact_old, exact_new = consolidate_exact_entity_ids(
                    old_entities_for_relations,
                    generated_entities,
                    context_word_count=_context_word_count,
                )
                merge_calls = merge_call_specs(exact_new, exact_old)

                def update_entity_merge_progress(completed: int) -> None:
                    percent = (
                        min(100, max(0, int(completed * 100 / len(merge_calls))))
                        if merge_calls
                        else 100
                    )
                    _transition_job(
                        settings,
                        state["job_id"],
                        "graph-entity-merging",
                        detail=(
                            "Generating redundant entity merge proposals: "
                            f"{percent}%"
                        ),
                    )

                update_entity_merge_progress(0)
                with _job_heartbeat(settings, state["job_id"]):
                    merge_results = _run_bounded_calls(
                        settings,
                        state["job_id"],
                        merge_calls,
                        lambda pair: entity_worker(pair, "propose_merges"),
                        thread_name_prefix="entity-merging",
                        on_complete=lambda completed, _total: update_entity_merge_progress(
                            completed
                        ),
                    )
                _transition_job(
                    settings,
                    state["job_id"],
                    "graph-entity-merging",
                    detail="Applying redundant entity merges",
                )
                merge_proposals = [
                    proposal for result in merge_results for proposal in result
                ]
                entities, surviving_new_entities = apply_entity_merges(
                    exact_old,
                    exact_new,
                    merge_proposals,
                    context_word_count=_context_word_count,
                )

                relation_new_entities = [
                    _entity_relation_evidence(
                        entity,
                        filenames_by_property_id,
                    )
                    for entity in surviving_new_entities
                ]
                relation_old_entities = [
                    _entity_relation_evidence(
                        entity,
                        filenames_by_property_id,
                    )
                    for entity in exact_old
                ]
                within_relation_calls, cross_relation_calls = relation_call_specs(
                    relation_new_entities,
                    relation_old_entities,
                )
                relation_total = len(within_relation_calls) + len(cross_relation_calls)

                def update_entity_relation_progress(completed: int) -> None:
                    percent = (
                        min(100, max(0, int(completed * 100 / relation_total)))
                        if relation_total
                        else 100
                    )
                    _transition_job(
                        settings,
                        state["job_id"],
                        "graph-entity-relations",
                        detail=f"Generating relations for entities: {percent}%",
                    )

                update_entity_relation_progress(0)
                with _job_heartbeat(settings, state["job_id"]):
                    within_relation_results = _run_bounded_calls(
                        settings,
                        state["job_id"],
                        within_relation_calls,
                        lambda pair: entity_worker(pair, "generate_relation_edges"),
                        thread_name_prefix="entity-relations-within",
                        on_complete=lambda completed, _total: update_entity_relation_progress(
                            completed
                        ),
                    )
                    cross_relation_results = _run_bounded_calls(
                        settings,
                        state["job_id"],
                        cross_relation_calls,
                        lambda pair: entity_worker(pair, "generate_relation_edges"),
                        thread_name_prefix="entity-relations-cross",
                        on_complete=lambda completed, _total: update_entity_relation_progress(
                            len(within_relation_calls) + completed
                        ),
                    )
                update_entity_relation_progress(relation_total)
                final_entity_ids = {
                    str(entity.get("id") or "") for entity in entities
                }
                old_entity_ids = {
                    str(entity.get("id") or "") for entity in exact_old
                }
                preserved_entity_edges = [
                    edge
                    for edge in current_entity_edges
                    if str(edge.get("source") or "") in old_entity_ids
                    and str(edge.get("target") or "") in old_entity_ids
                    and str(edge.get("source") or "") in final_entity_ids
                    and str(edge.get("target") or "") in final_entity_ids
                ]
                entity_edges = deduplicate_edges(
                    [
                        *preserved_entity_edges,
                        *_flatten_call_results(within_relation_results),
                        *_flatten_call_results(cross_relation_results),
                    ]
                )

            finally:
                # The entity stage no longer creates temporary extraction files.
                pass

            _transition_job(
                settings,
                state["job_id"],
                "graph-entity-embedding",
                detail=f"Updating {len(entities)} entity vectors",
            )
            if embedder is not None and entities:
                entity_vectors = embeddings_for_texts(
                    [entity_embedding_text(entity) for entity in entities], embedder
                )
                entities = [
                    {**entity, "embedding": vector}
                    for entity, vector in zip(entities, entity_vectors)
                ]
        finally:
            if embedder is not None:
                close = getattr(embedder, "close", None)
                if close:
                    close()
        _transition_job(
            settings,
            state["job_id"],
            "graph-snapshot",
            detail=f"Writing {len(properties)} properties and {len(entities)} entities",
        )
        snapshot_id = str(uuid.uuid4())
        graph_store.write_snapshot(
            GraphSnapshot(
                project_id,
                snapshot_id,
                properties,
                entities,
                property_edges,
                entity_edges,
                property_groups,
            )
        )
        _update_job(settings, state["job_id"], candidate_snapshot=snapshot_id)
        _raise_if_cancelled(settings, state["job_id"])
        return {
            "snapshot_id": snapshot_id,
            "entities": entities,
            "entity_edges": entity_edges,
            "properties": properties,
            "property_edges": property_edges,
        }

    def activate(state: PipelineState):
        settings, project_id, job_id = state["settings"], state["project_id"], state["job_id"]
        with connect(settings.sqlite_path) as db:
            job = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job and job["status"] == "cancelled":
            release_lock(settings, project_id, job_id)
            return {}
        _transition_job(
            settings,
            job_id,
            "graph-activate",
            detail="Activating candidate snapshot",
        )
        graph_store.activate(project_id, state["snapshot_id"])
        if state.get("operation") == "remove":
            PropertyCatalog(settings).delete(project_id, state["property_id"])
            delete_property_text(settings, project_id, state["property_id"])
        elif state.get("operation") == "batch-add":
            catalog = PropertyCatalog(settings)
            batch_property_ids = {
                item["property_id"] for item in state.get("batch_items") or []
            }
            grouped_rows = apply_group_placements(
                settings,
                project_id,
                catalog.list(project_id),
                state.get("directories") or {},
            )
            updated_at = datetime.now(timezone.utc).isoformat()
            catalog.replace_all(
                project_id,
                [
                    {
                        **row,
                        **(
                            {"status": "active", "updated_at": updated_at}
                            if row["id"] in batch_property_ids
                            else {}
                        ),
                    }
                    for row in grouped_rows
                ],
            )
            for item in state.get("batch_items") or []:
                write_property_text(
                    settings,
                    project_id,
                    item["property_id"],
                    item.get("text", ""),
                )
        else:
            catalog = PropertyCatalog(settings)
            current = catalog.get(project_id, state["property_id"])
            if not current:
                raise KeyError(state["property_id"])
            directory = state.get("directory", "")
            relative_path, _ = move_original(settings, project_id, current["relative_path"], directory, current["filename"])
            catalog.update(project_id, state["property_id"], {"definition": state["definition"], "relative_path": relative_path, "directory": directory, "status": "active", "updated_at": datetime.now(timezone.utc).isoformat()})
            write_property_text(
                settings,
                project_id,
                state["property_id"],
                state.get("text", ""),
            )
        _transition_job(
            settings,
            job_id,
            "active",
            status="completed",
            detail="Candidate snapshot active",
            active_snapshot=state["snapshot_id"],
        )
        if state.get("operation") == "remove":
            Path(state["path"]).unlink(missing_ok=True)
        release_lock(settings, project_id, job_id)
        return {}

    def first_stage(state: PipelineState) -> str:
        if state.get("operation") == "batch-add" and state.get("directories"):
            return "graphs"
        return "dg"

    builder.add_node("dg", dg).add_node("ga", ga).add_node("graphs", graphs).add_node("activate", activate)
    builder.add_conditional_edges(
        START,
        first_stage,
        {"dg": "dg", "graphs": "graphs"},
    )
    builder.add_edge("dg", "ga").add_edge("ga", "graphs").add_edge("graphs", "activate").add_edge("activate", END)
    return builder.compile()


def run_pipeline(
    settings: Settings,
    project_id: str,
    property_id: str,
    job_id: str,
    filename: str,
    kind: str,
    path: Path,
    comment: str = "",
    operation: str = "add",
    definition_override: str = "",
    extraction_path: str = "",
) -> None:
    graph_store = Neo4jGraphStore(settings)
    try:
        with connect(settings.sqlite_path) as db:
            db.execute("UPDATE jobs SET routes_json=? WHERE id=?", (json.dumps(provider_route_metadata(settings), sort_keys=True), job_id))
        if not path.is_file():
            raise FileNotFoundError(path)
        _raise_if_cancelled(settings, job_id)
        _transition_job(
            settings,
            job_id,
            "queued",
            detail="Preparing property pipeline",
        )
        full_text = (
            ""
            if operation == "remove"
            else extract_text(path, kind)
            if operation == "replace"
            else _property_content(settings, project_id, property_id, path, kind)
        )
        extraction_store = TemporaryExtractionStore(settings)
        existing_entities = (
            (graph_store.graph(project_id, "entity") or {}).get("nodes", [])
            if operation != "remove"
            else []
        )
        selection = None
        if full_text:
            if extraction_path and Path(extraction_path).is_file():
                try:
                    selection = extraction_store.load(extraction_path)
                except (OSError, ValueError, json.JSONDecodeError):
                    selection = None
            if selection is None:
                selection = select_extraction_text(
                    full_text,
                    filename=filename,
                    definition=definition_override,
                    import_context=comment,
                    existing_entities=existing_entities,
                )
                extraction_path = str(
                    extraction_store.save(
                        project_id, job_id, property_id, selection
                    )
                )
        state: PipelineState = {
            "settings": settings,
            "project_id": project_id,
            "property_id": property_id,
            "job_id": job_id,
            "filename": filename,
            "kind": kind,
            "path": str(path),
            "comment": comment,
            "operation": operation,
            "definition_override": definition_override,
            "extraction_path": extraction_path,
            "extraction_text": selection.text if selection is not None else "",
            "text": full_text,
        }
        build_workflow(graph_store).invoke(state)
    except JobCancelled:
        release_lock(settings, project_id, job_id)
    except Exception as exc:
        _transition_job(
            settings,
            job_id,
            "failed",
            status="failed",
            detail=str(exc),
            error=str(exc),
            error_detail=traceback.format_exc(),
            llm_response=extract_model_response(exc),
        )
        try:
            PropertyCatalog(settings).update(project_id, property_id, {"status": "failed", "updated_at": datetime.now(timezone.utc).isoformat()})
        except KeyError:
            pass
        release_lock(settings, project_id, job_id)
    finally:
        TemporaryExtractionStore(settings).delete(extraction_path)
        graph_store.close()


def run_property_removal(
    settings: Settings,
    project_id: str,
    property_id: str,
    job_id: str,
    path: Path,
) -> None:
    graph_store = Neo4jGraphStore(settings)
    try:
        _transition_job(
            settings,
            job_id,
            "graph-prune",
            detail="Removing property graph nodes and relations",
        )
        snapshot_id = str(uuid.uuid4())
        snapshot = prune_property_snapshot(
            graph_store,
            project_id,
            property_id,
            snapshot_id,
        )
        graph_store.write_snapshot(snapshot)
        _update_job(settings, job_id, candidate_snapshot=snapshot_id)
        _raise_if_cancelled(settings, job_id)
        _transition_job(
            settings,
            job_id,
            "graph-activate",
            detail="Activating pruned graph snapshot",
        )
        graph_store.activate(project_id, snapshot_id)
        PropertyCatalog(settings).delete(project_id, property_id)
        delete_property_text(settings, project_id, property_id)
        Path(path).unlink(missing_ok=True)
        _transition_job(
            settings,
            job_id,
            "active",
            status="completed",
            detail="Property removed from both graphs",
            active_snapshot=snapshot_id,
        )
        release_lock(settings, project_id, job_id)
    except JobCancelled:
        release_lock(settings, project_id, job_id)
    except Exception as exc:
        _transition_job(
            settings,
            job_id,
            "failed",
            status="failed",
            detail=str(exc),
            error=str(exc),
            error_detail=traceback.format_exc(),
            llm_response=extract_model_response(exc),
        )
        try:
            PropertyCatalog(settings).update(
                project_id,
                property_id,
                {
                    "status": "failed",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except KeyError:
            pass
        release_lock(settings, project_id, job_id)
    finally:
        graph_store.close()


def run_property_removals(
    settings: Settings,
    project_id: str,
    property_ids: list[str],
    job_id: str,
    paths: list[Path],
) -> None:
    graph_store = Neo4jGraphStore(settings)
    try:
        _transition_job(
            settings,
            job_id,
            "graph-prune",
            detail=f"Removing {len(property_ids)} properties from both graphs",
        )
        snapshot_id = str(uuid.uuid4())
        snapshot = prune_properties_snapshot(
            graph_store,
            project_id,
            property_ids,
            snapshot_id,
        )
        graph_store.write_snapshot(snapshot)
        _update_job(settings, job_id, candidate_snapshot=snapshot_id)
        _raise_if_cancelled(settings, job_id)
        _transition_job(
            settings,
            job_id,
            "graph-activate",
            detail="Activating pruned graph snapshot",
        )
        graph_store.activate(project_id, snapshot_id)
        catalog = PropertyCatalog(settings)
        for property_id, path in zip(property_ids, paths):
            catalog.delete(project_id, property_id)
            delete_property_text(settings, project_id, property_id)
            path.unlink(missing_ok=True)
        _transition_job(
            settings,
            job_id,
            "active",
            status="completed",
            detail=f"{len(property_ids)} properties removed from both graphs",
            active_snapshot=snapshot_id,
        )
        release_lock(settings, project_id, job_id)
    except JobCancelled:
        release_lock(settings, project_id, job_id)
    except Exception as exc:
        _transition_job(
            settings,
            job_id,
            "failed",
            status="failed",
            detail=str(exc),
            error=str(exc),
            error_detail=traceback.format_exc(),
            llm_response=extract_model_response(exc),
        )
        catalog = PropertyCatalog(settings)
        failed_at = datetime.now(timezone.utc).isoformat()
        for property_id in property_ids:
            try:
                catalog.update(
                    project_id,
                    property_id,
                    {"status": "failed", "updated_at": failed_at},
                )
            except KeyError:
                pass
        release_lock(settings, project_id, job_id)
    finally:
        graph_store.close()


def run_batch_pipeline(
    settings: Settings,
    project_id: str,
    job_id: str,
    items: list[dict],
    resume_snapshot_id: str | None = None,
    completed_property_ids: list[str] | None = None,
    directories: dict[str, str] | None = None,
) -> None:
    graph_store = Neo4jGraphStore(settings)
    property_ids = [item["property_id"] for item in items]
    try:
        if not items:
            raise ValueError("Property import batch is empty")
        with connect(settings.sqlite_path) as db:
            db.execute(
                "UPDATE jobs SET routes_json=? WHERE id=?",
                (
                    json.dumps(provider_route_metadata(settings), sort_keys=True),
                    job_id,
                ),
            )
        prepared_items = []
        for item in items:
            path = Path(item["path"])
            if not path.is_file():
                raise FileNotFoundError(path)
            prepared_items.append(
                {
                    **item,
                    "path": str(path),
                    "text": (
                        item["text"]
                        if isinstance(item.get("text"), str)
                        else _property_content(
                            settings,
                            project_id,
                            item["property_id"],
                            path,
                            item["kind"],
                        )
                    ),
                }
            )
        _raise_if_cancelled(settings, job_id)
        _transition_job(
            settings,
            job_id,
            "queued",
            detail=f"Preparing {len(prepared_items)} imported properties",
        )
        planned_directories = (
            directories
            if directories is not None
            else {
                item["property_id"]: str(item.get("directory") or "")
                for item in prepared_items
            }
            if all("directory" in item for item in prepared_items)
            else {}
        )
        if planned_directories:
            _merge_job_progress(settings, job_id, directories=planned_directories)
        state: PipelineState = {
            "settings": settings,
            "project_id": project_id,
            "job_id": job_id,
            "operation": "batch-add",
            "batch_items": prepared_items,
            "filename": prepared_items[0]["filename"],
            "resume_snapshot_id": resume_snapshot_id or "",
            "completed_property_ids": completed_property_ids or [],
            "directories": planned_directories,
        }
        build_workflow(graph_store).invoke(state)
    except JobCancelled:
        release_lock(settings, project_id, job_id)
    except Exception as exc:
        _transition_job(
            settings,
            job_id,
            "failed",
            status="failed",
            detail=str(exc),
            error=str(exc),
            error_detail=traceback.format_exc(),
            llm_response=extract_model_response(exc),
        )
        catalog = PropertyCatalog(settings)
        failed_at = datetime.now(timezone.utc).isoformat()
        with connect(settings.sqlite_path) as db:
            failed_job = db.execute(
                "SELECT progress_json FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        progress = _json_object(failed_job["progress_json"] if failed_job else None)
        completed = set(progress.get("completed_property_ids") or [])
        failed_property_ids = [
            property_id for property_id in property_ids if property_id not in completed
        ] or property_ids[-1:]
        for property_id in failed_property_ids:
            try:
                catalog.update(
                    project_id,
                    property_id,
                    {"status": "failed", "updated_at": failed_at},
                )
            except KeyError:
                pass
        release_lock(settings, project_id, job_id)
    finally:
        extraction_store = TemporaryExtractionStore(settings)
        for item in items:
            extraction_store.delete(item.get("extraction_path"))
            property_id = str(item.get("property_id") or "")
            if property_id:
                extraction_store.delete(
                    extraction_store.path(project_id, job_id, property_id)
                )
        graph_store.close()
