import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..db import connect
from ..security import require_capability
from ..services.graph_store import DEFAULT_ENTITY_PROMPT, DEFAULT_ENTITY_SCHEMA, Neo4jGraphStore
from ..services.providers import ProviderError, probe_provider_profile, save_provider_secret

router = APIRouter(prefix="/system", tags=["system"])


class SystemConfigUpdate(BaseModel):
    dg_agent_route: str | None = None
    ga_agent_route: str | None = None
    pgb_agent_route: str | None = None
    entity_agent_route: str | None = None
    ai_query_route: str | None = None
    shared_embedding_route: str | None = None
    entity_schema: str | None = None
    entity_prompt: str | None = None
    mcp_enabled: bool | None = None


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
    return {"routes": {key: values.get(key) for key in ("dg_agent_route", "ga_agent_route", "pgb_agent_route", "entity_agent_route", "ai_query_route", "shared_embedding_route")}, "entity_schema": values.get("entity_schema", DEFAULT_ENTITY_SCHEMA), "entity_prompt": values.get("entity_prompt", DEFAULT_ENTITY_PROMPT), "neo4j": {"uri": settings.neo4j_uri, "property_database": settings.neo4j_property_database, "entity_database": settings.neo4j_entity_database, "use_neo4j": settings.use_neo4j}, "mcp": {"enabled": mcp_enabled}}


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
            db.execute("INSERT INTO system_config(key,value,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (key, value, now))
    return get_config(settings, user)


@router.get("/providers")
def list_provider_profiles(settings: Settings = Depends(get_settings), user=Depends(require_capability("system.config.view"))):
    with connect(settings.sqlite_path) as db:
        rows = db.execute("SELECT id,name,provider_type,model,base_url,secret_configured,created_at,updated_at FROM provider_profiles ORDER BY name").fetchall()
    return [{**dict(row), "secret_configured": bool(row["secret_configured"])} for row in rows]


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
