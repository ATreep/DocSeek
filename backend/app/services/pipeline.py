from __future__ import annotations

import json
import base64
import mimetypes
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import Settings
from ..db import connect
from ..api.projects import release_lock
from .agents import DGAgent, GAAgent, PGBAgent, validate_edge_proposals
from .catalog import PropertyCatalog
from .extraction_text import (
    DEFAULT_EXTRACTION_TEXT_MAX_CHARS,
    ExtractionSelection,
    TemporaryExtractionStore,
    select_extraction_text,
)
from .grouping import apply_group_placements
from .model_errors import extract_model_response
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
    prune_property_snapshot,
)
from .providers import chat_provider, embedding_provider, provider_route_metadata
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
        if current and current["stage"] and current["stage_started_at"]:
            try:
                started = datetime.fromisoformat(current["stage_started_at"])
            except ValueError:
                started = None
            if started is not None:
                elapsed = max(0.0, (now - started).total_seconds())
                timings[current["stage"]] = round(
                    timings.get(current["stage"], 0.0) + elapsed, 3
                )
        fields = {
            "stage": stage,
            "status": status,
            "heartbeat": now.isoformat(),
            "stage_started_at": now.isoformat(),
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
            if item.get("id")
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
        current_entity_graph = graph_store.graph(
            project_id, "entity", resume_snapshot_id
        ) or {}
        current_entities = current_entity_graph.get("nodes", [])
        current_entity_edges = current_entity_graph.get("edges", [])
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
        extraction_store = TemporaryExtractionStore(settings)
        embedder = embedding_provider(settings, route_key="shared_embedding_route")
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
            entity_llm = chat_provider(
                settings,
                route_key="entity_agent_route",
                timeout=settings.entity_agent_timeout_seconds,
            )
            entity_builder = GraphRAGBuilder(
                config.get("entity_schema", DEFAULT_ENTITY_SCHEMA),
                config.get("entity_prompt", DEFAULT_ENTITY_PROMPT),
                settings.neo4j_entity_database,
                llm=entity_llm,
            )
            _transition_job(
                settings,
                state["job_id"],
                "graph-entity-extraction",
                detail=(
                    f"Preparing {len(entity_documents)} new properties for entity extraction"
                    if is_batch_add
                    else f"Analyzing new property against {len(current_entities)} existing entities"
                    if incremental_entity_add
                    else f"Analyzing {len(entity_documents)} text documents"
                ),
            )
            try:
                temporary_extraction_paths: list[Path] = []
                if is_batch_add:
                    entities = current_entities
                    entity_edges = current_entity_edges
                    checkpoint_snapshot_id = (
                        resume_snapshot_id or f"{state['job_id']}-checkpoint"
                    )
                    total_batch_items = len(batch_property_ids)
                    for document in entity_documents:
                        _raise_if_cancelled(settings, state["job_id"])
                        prepared_document, selection = _prepare_entity_document(
                            document,
                            entities,
                        )
                        extraction_path = extraction_store.save(
                            project_id,
                            state["job_id"],
                            document["property_id"],
                            selection,
                        )
                        temporary_extraction_paths.append(extraction_path)
                        filename = next(
                            (
                                item["filename"]
                                for item in batch_items
                                if item["property_id"] == document["property_id"]
                            ),
                            document["property_id"],
                        )
                        _transition_job(
                            settings,
                            state["job_id"],
                            "graph-entity-extraction",
                            detail=(
                                f"Generating graph nodes and edges {len(completed_property_ids) + 1}/{total_batch_items}: "
                                f"{filename}"
                            ),
                        )
                        with _job_heartbeat(settings, state["job_id"]):
                            delta_entities, delta_edges = entity_builder.build(
                                [prepared_document],
                                embedder=None,
                                current_entities=entities,
                                incremental=True,
                            )
                        extraction_store.delete(extraction_path)
                        entities, entity_edges = _merge_entity_delta(
                            entities,
                            entity_edges,
                            delta_entities,
                            delta_edges,
                        )
                        completed_property_ids.add(document["property_id"])
                        checkpoint_property_edges = [
                            edge
                            for edge in active_property_graph.get("edges", [])
                            if edge.get("source") in {item["id"] for item in properties}
                            and edge.get("target") in {item["id"] for item in properties}
                        ]
                        graph_store.write_snapshot(
                            GraphSnapshot(
                                project_id,
                                checkpoint_snapshot_id,
                                properties,
                                entities,
                                checkpoint_property_edges,
                                entity_edges,
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
                            completed_property_ids=sorted(completed_property_ids),
                            directories=state.get("directories") or {},
                            candidate_snapshot=checkpoint_snapshot_id,
                        )
                else:
                    bounded_entity_documents = []
                    extraction_paths = []
                    for document in entity_documents:
                        prepared_document, selection = _prepare_entity_document(
                            document,
                            current_entities,
                        )
                        bounded_entity_documents.append(prepared_document)
                        extraction_path = extraction_store.save(
                            project_id,
                            state["job_id"],
                            document["property_id"],
                            selection,
                        )
                        extraction_paths.append(extraction_path)
                        temporary_extraction_paths.append(extraction_path)
                    with _job_heartbeat(settings, state["job_id"]):
                        entities, entity_edges = entity_builder.build(
                            bounded_entity_documents,
                            embedder=None,
                            current_entities=current_entities,
                            incremental=incremental_entity_add,
                        )
                    for extraction_path in extraction_paths:
                        extraction_store.delete(extraction_path)
            finally:
                for extraction_path in temporary_extraction_paths:
                    extraction_store.delete(extraction_path)
                if entity_llm is not None:
                    close = getattr(entity_llm, "close", None)
                    if close:
                        close()
            if incremental_entity_add and not is_batch_add:
                entities, entity_edges = _merge_entity_delta(
                    current_entities,
                    current_entity_edges,
                    entities,
                    entity_edges,
                )
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
            "graph-property-relations",
            detail=f"Relating {len(properties)} property nodes",
        )
        pgb_agent = PGBAgent(settings=settings)
        try:
            property_edges = validate_edge_proposals(
                properties, pgb_agent.propose(properties)
            )
        finally:
            provider = getattr(pgb_agent, "provider", None)
            close = getattr(provider, "close", None)
            if close:
                close()
        _transition_job(
            settings,
            state["job_id"],
            "graph-snapshot",
            detail=f"Writing {len(properties)} properties and {len(entities)} entities",
        )
        snapshot_id = str(uuid.uuid4())
        graph_store.write_snapshot(GraphSnapshot(project_id, snapshot_id, properties, entities, property_edges, entity_edges))
        _update_job(settings, state["job_id"], candidate_snapshot=snapshot_id)
        _raise_if_cancelled(settings, state["job_id"])
        return {"snapshot_id": snapshot_id, "entities": entities, "entity_edges": entity_edges, "properties": properties, "property_edges": property_edges}

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

    builder.add_node("dg", dg).add_node("ga", ga).add_node("graphs", graphs).add_node("activate", activate)
    builder.add_edge(START, "dg").add_edge("dg", "ga").add_edge("ga", "graphs").add_edge("graphs", "activate").add_edge("activate", END)
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
        state: PipelineState = {
            "settings": settings,
            "project_id": project_id,
            "job_id": job_id,
            "operation": "batch-add",
            "batch_items": prepared_items,
            "filename": prepared_items[0]["filename"],
            "resume_snapshot_id": resume_snapshot_id or "",
            "completed_property_ids": completed_property_ids or [],
            "directories": directories or {},
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
