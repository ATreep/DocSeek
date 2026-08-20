import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..db import connect
from ..security import require_capability
from ..services.graph_store import DEFAULT_ENTITY_PROMPT, DEFAULT_ENTITY_SCHEMA, Neo4jGraphStore
from ..services.parallelism import (
    MAX_BATCH_LLM_CONCURRENCY,
    batch_llm_concurrency_from_values,
)
from ..services.providers import ProviderError, probe_provider_profile, save_provider_secret
from ..services.query_history import history_compaction_token_threshold_from_values
from ..services.retrieval_limits import (
    MAX_RETRIEVAL_LIMIT_PER_KIND,
    MAX_RETRIEVAL_TOTAL_NODE_LIMIT,
    retrieval_limits_from_values,
)

router = APIRouter(prefix="/system", tags=["system"])

IMPORT_PROVIDER_ROUTES = (
    ("dg_agent_route", "Definition Generation Agent", "llm"),
    ("ga_agent_route", "Group Arrangement Agent", "llm"),
    ("entity_agent_route", "Entity Extraction Agent", "llm"),
    ("shared_embedding_route", "Shared Embedding Model", "embedding"),
)


class SystemConfigUpdate(BaseModel):
    dg_agent_route: str | None = None
    ga_agent_route: str | None = None
    entity_agent_route: str | None = None
    ai_query_route: str | None = None
    shared_embedding_route: str | None = None
    entity_schema: str | None = None
    entity_prompt: str | None = None
    mcp_enabled: bool | None = None
    batch_llm_concurrency: int | None = Field(
        default=None, ge=1, le=MAX_BATCH_LLM_CONCURRENCY
    )
    ai_query_history_compaction_token_threshold: int | None = Field(
        default=None,
        ge=1,
    )
    ai_query_property_limit: int | None = Field(
        default=None, ge=1, le=MAX_RETRIEVAL_LIMIT_PER_KIND
    )
    ai_query_entity_limit: int | None = Field(
        default=None, ge=1, le=MAX_RETRIEVAL_LIMIT_PER_KIND
    )
    ai_query_total_node_limit: int | None = Field(
        default=None, ge=1, le=MAX_RETRIEVAL_TOTAL_NODE_LIMIT
    )
    search_property_limit: int | None = Field(
        default=None, ge=1, le=MAX_RETRIEVAL_LIMIT_PER_KIND
    )
    search_entity_limit: int | None = Field(
        default=None, ge=1, le=MAX_RETRIEVAL_LIMIT_PER_KIND
    )


class ProviderProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider_type: str = Field(pattern="^(llm|embedding)$")
    model: str = Field(min_length=1, max_length=200)
    base_url: str | None = None
    secret: str | None = None


class ProviderProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = None
    secret: str | None = None


@router.get("/config")
def get_config(settings: Settings = Depends(get_settings), user=Depends(require_capability("system.config.view"))):
    with connect(settings.sqlite_path) as db:
        values = {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM system_config")}
    mcp_enabled = str(values.get("mcp_enabled", "true")).lower() in {"1", "true", "yes", "on"}
    retrieval = retrieval_limits_from_values(values)
    return {"routes": {key: values.get(key) for key in ("dg_agent_route", "ga_agent_route", "entity_agent_route", "ai_query_route", "shared_embedding_route")}, "entity_schema": values.get("entity_schema", DEFAULT_ENTITY_SCHEMA), "entity_prompt": values.get("entity_prompt", DEFAULT_ENTITY_PROMPT), "neo4j": {"uri": settings.neo4j_uri, "property_database": settings.neo4j_property_database, "entity_database": settings.neo4j_entity_database, "use_neo4j": settings.use_neo4j}, "mcp": {"enabled": mcp_enabled}, "batch_llm_concurrency": batch_llm_concurrency_from_values(values, default=int(settings.batch_llm_concurrency)), "ai_query_history_compaction_token_threshold": history_compaction_token_threshold_from_values(values), "retrieval": retrieval.as_system_config()}


@router.get("/import-provider-readiness")
def import_provider_readiness(
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("property.upload")),
):
    route_keys = tuple(key for key, _, _ in IMPORT_PROVIDER_ROUTES)
    placeholders = ",".join("?" for _ in route_keys)
    with connect(settings.sqlite_path) as db:
        routes = {
            row["key"]: row["value"]
            for row in db.execute(
                f"SELECT key,value FROM system_config WHERE key IN ({placeholders})",
                route_keys,
            )
        }
        profiles = {
            row["id"]: row
            for row in db.execute(
                "SELECT id,provider_type,model,base_url,secret_configured FROM provider_profiles"
            )
        }

    missing_routes = []
    for key, label, expected_type in IMPORT_PROVIDER_ROUTES:
        profile = profiles.get(routes.get(key))
        configured = bool(
            profile
            and profile["provider_type"] == expected_type
            and profile["model"]
            and profile["base_url"]
            and profile["secret_configured"]
        )
        if not configured:
            missing_routes.append(
                {"key": key, "label": label, "provider_type": expected_type}
            )

    return {
        "ready": not missing_routes,
        "missing_routes": missing_routes,
        "can_configure": {
            "system.config.view",
            "system.config.edit",
        }.issubset(user["capabilities"]),
    }


@router.patch("/config")
def update_config(payload: SystemConfigUpdate, settings: Settings = Depends(get_settings), user=Depends(require_capability("system.config.edit"))):
    now = datetime.now(timezone.utc).isoformat()
    with connect(settings.sqlite_path) as db:
        values = payload.model_dump()
        for key in payload.model_fields_set:
            value = values[key]
            if key.endswith("_route"):
                if value is None:
                    db.execute("DELETE FROM system_config WHERE key=?", (key,))
                    continue
                profile = db.execute("SELECT provider_type FROM provider_profiles WHERE id=?", (value,)).fetchone()
                expected_type = "embedding" if key == "shared_embedding_route" else "llm"
                if not profile or profile["provider_type"] != expected_type:
                    raise HTTPException(status_code=422, detail=f"{key} must reference an existing {expected_type} provider profile")
            if value is None:
                continue
            db.execute("INSERT INTO system_config(key,value,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (key, str(value), now))
    return get_config(settings, user)


@router.get("/providers")
def list_provider_profiles(settings: Settings = Depends(get_settings), user=Depends(require_capability("system.config.view"))):
    with connect(settings.sqlite_path) as db:
        rows = db.execute("SELECT id,name,provider_type,model,base_url,secret_configured,created_at,updated_at FROM provider_profiles ORDER BY name").fetchall()
    return [{**dict(row), "secret_configured": bool(row["secret_configured"])} for row in rows]


@router.get("/llm-invocations")
def list_llm_invocations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("system.config.view")),
):
    with connect(settings.sqlite_path) as db:
        rows = db.execute(
            """SELECT id,request_time,response_time,duration_ms,model,route_key,
                      profile_id,status,request_prompt,response_output
               FROM llm_invocation_logs
               ORDER BY request_time DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


@router.post("/providers", status_code=201)
def create_provider_profile(payload: ProviderProfileCreate, settings: Settings = Depends(get_settings), user=Depends(require_capability("system.config.edit"))):
    profile_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with connect(settings.sqlite_path) as db:
        try:
            db.execute("INSERT INTO provider_profiles(id,name,provider_type,model,base_url,secret_configured,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (profile_id, payload.name.strip(), payload.provider_type, payload.model.strip(), payload.base_url, int(bool(payload.secret)), now, now))
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(status_code=409, detail="Provider profile name already exists") from exc
            raise
    save_provider_secret(settings, profile_id, payload.secret)
    return {"id": profile_id, "name": payload.name.strip(), "provider_type": payload.provider_type, "model": payload.model.strip(), "base_url": payload.base_url, "secret_configured": bool(payload.secret)}


@router.patch("/providers/{profile_id}")
def update_provider_profile(profile_id: str, payload: ProviderProfileUpdate, settings: Settings = Depends(get_settings), user=Depends(require_capability("system.config.edit"))):
    now = datetime.now(timezone.utc).isoformat()
    with connect(settings.sqlite_path) as db:
        existing = db.execute("SELECT id,name,provider_type,model,base_url,secret_configured FROM provider_profiles WHERE id=?", (profile_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Provider profile not found")
        values = {
            "name": payload.name.strip() if payload.name is not None else existing["name"],
            "model": payload.model.strip() if payload.model is not None else existing["model"],
            "base_url": payload.base_url,
        }
        if payload.base_url is None and "base_url" not in payload.model_fields_set:
            values["base_url"] = existing["base_url"]
        secret_configured = existing["secret_configured"]
        if "secret" in payload.model_fields_set:
            secret_configured = int(bool(payload.secret))
        try:
            db.execute(
                "UPDATE provider_profiles SET name=?,model=?,base_url=?,secret_configured=?,updated_at=? WHERE id=?",
                (values["name"], values["model"], values["base_url"], secret_configured, now, profile_id),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(status_code=409, detail="Provider profile name already exists") from exc
            raise
    if "secret" in payload.model_fields_set:
        save_provider_secret(settings, profile_id, payload.secret)
    return {"id": profile_id, "name": values["name"], "provider_type": existing["provider_type"], "model": values["model"], "base_url": values["base_url"], "secret_configured": bool(secret_configured)}


@router.delete("/providers/{profile_id}")
def delete_provider_profile(profile_id: str, settings: Settings = Depends(get_settings), user=Depends(require_capability("system.config.edit"))):
    with connect(settings.sqlite_path) as db:
        existing = db.execute("SELECT id FROM provider_profiles WHERE id=?", (profile_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Provider profile not found")
        db.execute("DELETE FROM system_config WHERE value=? AND key LIKE '%_route'", (profile_id,))
        db.execute("DELETE FROM provider_profiles WHERE id=?", (profile_id,))
    save_provider_secret(settings, profile_id, None)
    return {"deleted": True, "id": profile_id}


@router.post("/providers/{profile_id}/validate")
def validate_provider(profile_id: str, settings: Settings = Depends(get_settings), user=Depends(require_capability("system.config.edit"))):
    try:
        return probe_provider_profile(settings, profile_id)
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/neo4j/check")
def check_neo4j(settings: Settings = Depends(get_settings), user=Depends(require_capability("system.config.view"))):
    store = Neo4jGraphStore(settings)
    try:
        ready = store.using_neo4j
        configured = bool(settings.use_neo4j)
        return {
            "ready": ready,
            "fallback": not ready,
            "configured": configured,
            "mode": "neo4j" if ready else "local-fallback",
            "property_database": settings.neo4j_property_database,
            "entity_database": settings.neo4j_entity_database,
            "message": "Neo4j connection ready" if ready else ("Neo4j unavailable; local graph storage active" if configured else "Local graph storage active"),
        }
    finally:
        store.close()


@router.get("/storage/check")
def check_storage(settings: Settings = Depends(get_settings), user=Depends(require_capability("system.config.view"))):
    settings.ensure_directories()
    probe = settings.conf_dir / ".write-check"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError:
        writable = False
    return {"writable": writable, "data_dir": str(settings.data_dir), "projects_dir": str(settings.projects_dir)}
