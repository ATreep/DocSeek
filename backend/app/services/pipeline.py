from __future__ import annotations

import json
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
)
from .providers import chat_provider, embedding_provider, provider_route_metadata
from .parsers import extract_text
from .storage import move_original, safe_directory


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
        if state.get("operation") == "metadata":
            return {"definition": state.get("definition_override", "")}
        if state.get("operation") == "add" and state.get("definition_override"):
            return {"definition": state["definition_override"]}
        result = DGAgent(settings=state["settings"]).generate(state["filename"], state["kind"], state.get("text", ""), state.get("comment", ""))
        return {"definition": result.definition}

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
        active_property_graph = graph_store.graph(project_id, "property") or {}
        active_properties = {
            item["id"]: item
            for item in active_property_graph.get("nodes", [])
            if item.get("id")
        }
        incremental_entity_add = _should_extract_entities_incrementally(
            state.get("operation"), state.get("property_id"), active_properties
        )
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
            is_processed_property = row["id"] == state["property_id"]
            active_property = active_properties.get(row["id"])
            if is_processed_property:
                text = state.get("text", "")
            elif active_property is not None and isinstance(active_property.get("content"), str):
                text = active_property["content"]
            else:
                text = extract_text(path, row["property_type"])
            definition = state["definition"] if is_processed_property else (row.get("definition") or "")
            directory = state.get("directory", "") if is_processed_property else _directory_from_relative_path(row["relative_path"])
            relative_path = _relative_path(directory, row["filename"]) if is_processed_property else row["relative_path"]
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
            if row["property_type"] != "image":
                documents.append({"project_id": project_id, "property_id": row["id"], "property_type": row["property_type"], "text": text})
        with connect(settings.sqlite_path) as db:
            config = {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM system_config WHERE key IN ('entity_schema','entity_prompt')")}
        current_entity_graph = graph_store.graph(project_id, "entity") or {}
        current_entities = current_entity_graph.get("nodes", [])
        current_entity_edges = current_entity_graph.get("edges", [])
        entity_documents = (
            [
                document
                for document in documents
                if document["property_id"] == state["property_id"]
            ]
            if incremental_entity_add
            else documents
        )
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
                    f"Analyzing new property against {len(current_entities)} existing entities"
                    if incremental_entity_add
                    else f"Analyzing {len(entity_documents)} text documents"
                ),
            )
            try:
                with _job_heartbeat(settings, state["job_id"]):
                    entities, entity_edges = entity_builder.build(
                        entity_documents,
                        embedder=None,
                        current_entities=current_entities,
                        incremental=incremental_entity_add,
                    )
            finally:
                if entity_llm is not None:
                    close = getattr(entity_llm, "close", None)
                    if close:
                        close()
            if incremental_entity_add:
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
        else:
            catalog = PropertyCatalog(settings)
            current = catalog.get(project_id, state["property_id"])
            if not current:
                raise KeyError(state["property_id"])
            directory = state.get("directory", "")
            relative_path, _ = move_original(settings, project_id, current["relative_path"], directory, current["filename"])
            catalog.update(project_id, state["property_id"], {"definition": state["definition"], "relative_path": relative_path, "directory": directory, "status": "active", "updated_at": datetime.now(timezone.utc).isoformat()})
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


def run_pipeline(settings: Settings, project_id: str, property_id: str, job_id: str, filename: str, kind: str, path: Path, comment: str = "", operation: str = "add", definition_override: str = "") -> None:
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
            "text": "" if operation == "remove" else extract_text(path, kind),
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
        )
        try:
            PropertyCatalog(settings).update(project_id, property_id, {"status": "failed", "updated_at": datetime.now(timezone.utc).isoformat()})
        except KeyError:
            pass
        release_lock(settings, project_id, job_id)
    finally:
        graph_store.close()
