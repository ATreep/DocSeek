import uuid
import base64
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from ..config import Settings, get_settings
from ..db import connect
from ..security import get_current_user, require_capability
from ..services.catalog import PropertyCatalog
from ..services.graph_store import Neo4jGraphStore
from ..services.pipeline import _current_group_tree
from ..services.retrieval import Retriever
from ..services.storage import safe_filename
from .projects import get_project

router = APIRouter(prefix="/projects", tags=["mcp"])
transport_router = APIRouter(prefix="/mcp", tags=["mcp-transport"])
_active: dict[str, Any] | None = None
TOOLS = {
    "list_properties": "property.view", "get_property": "property.view", "get_property_attribute": "property.attribute.view",
    "add_property": "property.upload", "replace_property": "property.replace", "remove_property": "property.delete",
    "list_entities": "graph.entity.view", "get_entity": "graph.entity.view", "search_properties": "search.properties",
    "search_entities": "search.entities", "get_property_graph": "graph.property.view", "get_entity_graph": "graph.entity.view",
    "regroup_properties": "property.move", "get_processing_status": "agent.status.view",
}


def _entity_belongs_to_property(entity: dict[str, Any], property_id: str) -> bool:
    source_property_ids = entity.get("source_property_ids")
    if isinstance(source_property_ids, list) and property_id in source_property_ids:
        return True
    source_contexts = entity.get("source_contexts")
    return isinstance(source_contexts, list) and any(
        isinstance(context, dict) and context.get("property_id") == property_id
        for context in source_contexts
    )


def close_active_for_user(user_id: str) -> None:
    global _active
    if _active and _active["user_id"] == user_id:
        _active = None


def close_active_for_project(project_id: str) -> None:
    global _active
    if _active and _active["project_id"] == project_id:
        _active = None


@router.get("/{project_id}/mcp")
def mcp_status(project_id: str, settings: Settings = Depends(get_settings), user=Depends(require_capability("mcp.use"))):
    return {"open": bool(_active and _active["project_id"] == project_id), "project_id": project_id, "endpoint": _active["endpoint"] if _active and _active["project_id"] == project_id else None}


@router.post("/{project_id}/mcp/open")
def open_mcp(project_id: str, settings: Settings = Depends(get_settings), user=Depends(require_capability("mcp.use"))):
    global _active
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    with connect(settings.sqlite_path) as db:
        enabled = db.execute("SELECT value FROM system_config WHERE key='mcp_enabled'").fetchone()
    if enabled and str(enabled["value"]).lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=503, detail="MCP endpoints are disabled by system configuration")
    # Opening a new project endpoint always closes the old one.
    endpoint_id = str(uuid.uuid4())
    _active = {"project_id": project_id, "user_id": user["id"], "capabilities": set(user["capabilities"]), "endpoint_id": endpoint_id, "endpoint": f"/api/mcp/{project_id}/{endpoint_id}"}
    return {"open": True, "project_id": project_id, "endpoint": _active["endpoint"], "capabilities": sorted(_active["capabilities"])}


@router.post("/{project_id}/mcp/close")
def close_mcp(project_id: str, settings: Settings = Depends(get_settings), user=Depends(require_capability("mcp.use"))):
    global _active
    if _active and _active["project_id"] == project_id:
        _active = None
    return {"open": False, "project_id": project_id}


@router.post("/{project_id}/mcp/call/{tool}")
def call_mcp(project_id: str, tool: str, payload: dict | None = None, settings: Settings = Depends(get_settings), user=Depends(get_current_user)):
    if not _active or _active["project_id"] != project_id:
        raise HTTPException(status_code=409, detail="MCP is closed for this project")
    return _execute_tool(project_id, tool, payload or {}, settings, _active)


def _execute_tool(project_id: str, tool: str, payload: dict, settings: Settings, active: dict[str, Any], background_tasks: BackgroundTasks | None = None):
    if tool not in TOOLS:
        raise HTTPException(status_code=404, detail="Unknown MCP tool")
    required = TOOLS[tool]
    if required not in active["capabilities"]:
        raise HTTPException(status_code=403, detail=f"MCP opener lacks capability: {required}")
    catalog = PropertyCatalog(settings)
    store = Neo4jGraphStore(settings)
    if tool == "list_properties":
        return {"property_tree": _current_group_tree(catalog.list(project_id))}
    if tool == "get_property":
        item = catalog.get(project_id, payload.get("property_id", ""))
        if not item:
            raise HTTPException(status_code=404, detail="Property not found")
        return item
    if tool == "get_property_attribute":
        item = catalog.get(project_id, payload.get("property_id", ""))
        if not item:
            raise HTTPException(status_code=404, detail="Property not found")
        return {"property_id": item["id"], "definition": item.get("definition"), "property_type": item.get("property_type"), "status": item.get("status")}
    if tool == "get_property_graph":
        return store.graph(project_id, "property")
    if tool == "get_entity_graph":
        return store.graph(project_id, "entity")
    if tool == "list_entities":
        entities = store.graph(project_id, "entity")["nodes"]
        property_id = payload.get("property_id")
        if isinstance(property_id, str) and property_id.strip():
            entities = [
                entity
                for entity in entities
                if _entity_belongs_to_property(entity, property_id.strip())
            ]
        return {"entities": entities}
    if tool == "get_entity":
        entity_id = payload.get("entity_id", "")
        entity = next((item for item in store.graph(project_id, "entity")["nodes"] if item.get("id") == entity_id), None)
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")
        return entity
    if tool == "search_properties":
        return {"properties": Retriever(store).search_properties(project_id, payload.get("query", ""))}
    if tool == "search_entities":
        return {"entities": store.search(project_id, payload.get("query", ""), "entities")}
    if tool == "regroup_properties":
        revision_prompt = payload.get("revision_prompt")
        if (
            not isinstance(revision_prompt, str)
            or not revision_prompt.strip()
            or len(revision_prompt) > 4000
        ):
            raise HTTPException(
                status_code=422,
                detail="Re-grouping prompt must contain 1 to 4000 characters",
            )
        from .properties import (
            RegroupConfirmation,
            RegroupConfirmationItem,
            RegroupRequest,
            confirm_regroup_properties,
            regroup_properties,
        )

        proposal = regroup_properties(
            project_id,
            RegroupRequest(revision_prompt=revision_prompt.strip()),
            settings,
            active,
        )
        confirmed = confirm_regroup_properties(
            project_id,
            RegroupConfirmation(
                catalog_signature=proposal["catalog_signature"],
                items=[
                    RegroupConfirmationItem(
                        property_id=change["property_id"],
                        directory=change["proposed_directory"],
                        filename=change["proposed_filename"],
                    )
                    for change in proposal["changes"]
                ],
            ),
            settings,
            active,
        )
        return {**confirmed, "job_id": proposal.get("job_id")}
    if tool == "get_processing_status":
        with connect(settings.sqlite_path) as db:
            lock = db.execute("SELECT * FROM project_locks WHERE project_id=?", (project_id,)).fetchone()
            job = db.execute("SELECT * FROM jobs WHERE project_id=? ORDER BY heartbeat DESC LIMIT 1", (project_id,)).fetchone()
        return {"locked": lock is not None, **(dict(job) if job else {"status": "idle", "stage": None})}
    if tool in {"add_property", "replace_property"}:
        filename = safe_filename(payload.get("filename", "property.txt"))
        content = base64.b64decode(payload.get("content_base64", "")) if payload.get("content_base64") else payload.get("content", "").encode()
        if tool == "add_property":
            from .properties import _enqueue_property
            return _enqueue_property(settings, project_id, filename, content, payload.get("content_type"), payload.get("comment", ""), background_tasks)
        from .properties import _enqueue_replacement
        return _enqueue_replacement(settings, project_id, payload.get("property_id", ""), filename, content, payload.get("content_type"), background_tasks)
    if tool == "remove_property":
        from .properties import _enqueue_removal
        return _enqueue_removal(settings, project_id, payload.get("property_id", ""), background_tasks)
    raise HTTPException(status_code=404, detail="Unknown MCP tool")


@transport_router.post("/{project_id}/{endpoint_id}/{tool}")
def transport_call(project_id: str, endpoint_id: str, tool: str, payload: dict | None = None, background_tasks: BackgroundTasks = None, settings: Settings = Depends(get_settings)):
    if not _active or _active["project_id"] != project_id or _active["endpoint_id"] != endpoint_id:
        raise HTTPException(status_code=409, detail="MCP is closed for this project")
    return _execute_tool(project_id, tool, payload or {}, settings, _active, background_tasks)
