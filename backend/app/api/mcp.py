import uuid
import base64
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

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
MCP_PROTOCOL_VERSIONS = {"2025-03-26", "2025-06-18"}
LATEST_MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_TOOL_DEFINITIONS = (
    {
        "name": "list_properties",
        "description": "List the complete property tree for the open project.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_property",
        "description": "Get one property and its metadata by property ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"property_id": {"type": "string"}},
            "required": ["property_id"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_property_attribute",
        "description": "Get the definition, type, and status of one property.",
        "inputSchema": {
            "type": "object",
            "properties": {"property_id": {"type": "string"}},
            "required": ["property_id"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "add_property",
        "description": "Add a property file to the open project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "content": {"type": "string"},
                "content_base64": {"type": "string"},
                "content_type": {"type": "string"},
                "comment": {"type": "string"},
            },
            "required": ["filename"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "replace_property",
        "description": "Replace the file contents of an existing property.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "property_id": {"type": "string"},
                "filename": {"type": "string"},
                "content": {"type": "string"},
                "content_base64": {"type": "string"},
                "content_type": {"type": "string"},
            },
            "required": ["property_id", "filename"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "remove_property",
        "description": "Remove an existing property from the open project.",
        "inputSchema": {
            "type": "object",
            "properties": {"property_id": {"type": "string"}},
            "required": ["property_id"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "list_entities",
        "description": "List entities, optionally filtered to one source property.",
        "inputSchema": {
            "type": "object",
            "properties": {"property_id": {"type": "string"}},
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_entity",
        "description": "Get one entity by entity ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"entity_id": {"type": "string"}},
            "required": ["entity_id"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "search_properties",
        "description": "Search the properties in the open project.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "search_entities",
        "description": "Search the entities in the open project.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_property_graph",
        "description": "Get the property graph for the open project.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_entity_graph",
        "description": "Get the entity graph for the open project.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "regroup_properties",
        "description": "Rearrange the property tree using a natural-language instruction.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "revision_prompt": {"type": "string", "minLength": 1, "maxLength": 4000}
            },
            "required": ["revision_prompt"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "get_processing_status",
        "description": "Get the current processing state for the open project.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
)


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


def _matches_active_endpoint(project_id: str, endpoint_id: str) -> bool:
    return bool(
        _active
        and _active["project_id"] == project_id
        and _active["endpoint_id"] == endpoint_id
    )


def _origin_is_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    parsed = urlparse(origin)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }


def _jsonrpc_result(request_id: Any, result: Any) -> JSONResponse:
    return JSONResponse(
        jsonable_encoder({"jsonrpc": "2.0", "id": request_id, "result": result})
    )


def _jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    *,
    status_code: int = 200,
    data: Any | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return JSONResponse(
        jsonable_encoder({"jsonrpc": "2.0", "id": request_id, "error": error}),
        status_code=status_code,
    )


def _available_mcp_tools(active: dict[str, Any]) -> list[dict[str, Any]]:
    capabilities = active["capabilities"]
    return [
        definition
        for definition in MCP_TOOL_DEFINITIONS
        if TOOLS[definition["name"]] in capabilities
    ]


@transport_router.post("/{project_id}/{endpoint_id}")
async def streamable_http(
    project_id: str,
    endpoint_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
):
    active = _active
    if not (
        active
        and active["project_id"] == project_id
        and active["endpoint_id"] == endpoint_id
    ):
        return _jsonrpc_error(
            None,
            -32001,
            "MCP endpoint is closed or expired",
            status_code=404,
        )
    if not _origin_is_allowed(request):
        return _jsonrpc_error(None, -32000, "Invalid Origin", status_code=403)

    try:
        message = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _jsonrpc_error(None, -32700, "Parse error", status_code=400)

    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _jsonrpc_error(None, -32600, "Invalid Request", status_code=400)

    request_id = message.get("id")
    method = message.get("method")
    if method is None:
        return Response(status_code=202)
    if not isinstance(method, str):
        return _jsonrpc_error(request_id, -32600, "Invalid Request", status_code=400)
    if "id" not in message:
        return Response(status_code=202)

    params = message.get("params", {})
    if not isinstance(params, dict):
        return _jsonrpc_error(request_id, -32602, "Invalid params")

    if method == "initialize":
        requested_version = params.get("protocolVersion")
        protocol_version = (
            requested_version
            if requested_version in MCP_PROTOCOL_VERSIONS
            else LATEST_MCP_PROTOCOL_VERSION
        )
        return _jsonrpc_result(
            request_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "docseek-project-mcp",
                    "title": "DocSeek Project MCP",
                    "version": "0.1.0",
                },
                "instructions": "Use these tools to work with the currently open project.",
            },
        )

    protocol_version = request.headers.get("mcp-protocol-version", "2025-03-26")
    if protocol_version not in MCP_PROTOCOL_VERSIONS:
        return _jsonrpc_error(
            request_id,
            -32602,
            "Unsupported protocol version",
            status_code=400,
            data={"supported": sorted(MCP_PROTOCOL_VERSIONS), "requested": protocol_version},
        )

    if method == "ping":
        return _jsonrpc_result(request_id, {})
    if method == "tools/list":
        return _jsonrpc_result(request_id, {"tools": _available_mcp_tools(active)})
    if method == "tools/call":
        tool = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(tool, str) or not isinstance(arguments, dict):
            return _jsonrpc_error(request_id, -32602, "Invalid params")
        try:
            result = await run_in_threadpool(
                _execute_tool,
                project_id,
                tool,
                arguments,
                settings,
                active,
                background_tasks,
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail)
            return _jsonrpc_result(
                request_id,
                {"content": [{"type": "text", "text": detail}], "isError": True},
            )
        encoded_result = jsonable_encoder(result)
        return _jsonrpc_result(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(encoded_result, ensure_ascii=False),
                    }
                ],
                "structuredContent": encoded_result,
                "isError": False,
            },
        )
    return _jsonrpc_error(request_id, -32601, "Method not found")


@transport_router.get("/{project_id}/{endpoint_id}")
def streamable_http_events(project_id: str, endpoint_id: str, request: Request):
    if not _matches_active_endpoint(project_id, endpoint_id):
        return Response(status_code=404)
    if not _origin_is_allowed(request):
        return Response(status_code=403)
    return Response(status_code=405, headers={"Allow": "POST"})


@transport_router.delete("/{project_id}/{endpoint_id}")
def streamable_http_delete(project_id: str, endpoint_id: str, request: Request):
    if not _matches_active_endpoint(project_id, endpoint_id):
        return Response(status_code=404)
    if not _origin_is_allowed(request):
        return Response(status_code=403)
    return Response(status_code=405, headers={"Allow": "GET, POST"})


@transport_router.post("/{project_id}/{endpoint_id}/{tool}")
def transport_call(project_id: str, endpoint_id: str, tool: str, payload: dict | None = None, background_tasks: BackgroundTasks = None, settings: Settings = Depends(get_settings)):
    if not _active or _active["project_id"] != project_id or _active["endpoint_id"] != endpoint_id:
        raise HTTPException(status_code=409, detail="MCP is closed for this project")
    return _execute_tool(project_id, tool, payload or {}, settings, _active, background_tasks)
