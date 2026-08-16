import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..db import connect
from ..security import CAPABILITIES, hash_password, require_capability

router = APIRouter(prefix="/admin", tags=["administration"])


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=200)


class UserUpdate(BaseModel):
    disabled: bool | None = None


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MembershipRequest(BaseModel):
    user_id: str


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    capabilities: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    capabilities: list[str] | None = None


def _validate_capabilities(capabilities: list[str]) -> list[str]:
    invalid = sorted(set(capabilities) - CAPABILITIES)
    if invalid:
        raise HTTPException(status_code=422, detail={"invalid_capabilities": invalid})
    return sorted(set(capabilities))


@router.get("/users")
def list_users(settings: Settings = Depends(get_settings), user=Depends(require_capability("user.manage"))):
    with connect(settings.sqlite_path) as db:
        rows = db.execute("SELECT id,username,disabled,created_at FROM users ORDER BY username").fetchall()
    return [dict(row) for row in rows]


@router.post("/users", status_code=201)
def create_user(payload: UserCreate, settings: Settings = Depends(get_settings), user=Depends(require_capability("user.manage"))):
    user_id = str(uuid.uuid4())
    with connect(settings.sqlite_path) as db:
        try:
            db.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES (?,?,?,?)", (user_id, payload.username.strip(), hash_password(payload.password), datetime.now(timezone.utc).isoformat()))
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(status_code=409, detail="Username already exists") from exc
            raise
    return {"id": user_id, "username": payload.username.strip(), "disabled": False}


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate, settings: Settings = Depends(get_settings), user=Depends(require_capability("user.manage"))):
    if payload.disabled is None:
        raise HTTPException(status_code=422, detail="Provide disabled")
    with connect(settings.sqlite_path) as db:
        row = db.execute("SELECT id,username,disabled,created_at FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        db.execute("UPDATE users SET disabled=? WHERE id=?", (int(payload.disabled), user_id))
    return {**dict(row), "disabled": bool(payload.disabled)}


@router.get("/groups")
def list_groups(settings: Settings = Depends(get_settings), user=Depends(require_capability("group.manage"))):
    with connect(settings.sqlite_path) as db:
        rows = db.execute("SELECT id,name FROM groups ORDER BY name").fetchall()
    return [dict(row) for row in rows]


@router.post("/groups", status_code=201)
def create_group(payload: GroupCreate, settings: Settings = Depends(get_settings), user=Depends(require_capability("group.manage"))):
    group_id = str(uuid.uuid4())
    with connect(settings.sqlite_path) as db:
        try:
            db.execute("INSERT INTO groups(id,name) VALUES (?,?)", (group_id, payload.name.strip()))
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(status_code=409, detail="Group name already exists") from exc
            raise
    return {"id": group_id, "name": payload.name.strip()}


@router.post("/groups/{group_id}/members")
def add_group_member(group_id: str, payload: MembershipRequest, settings: Settings = Depends(get_settings), user=Depends(require_capability("group.manage"))):
    with connect(settings.sqlite_path) as db:
        if not db.execute("SELECT 1 FROM groups WHERE id=?", (group_id,)).fetchone() or not db.execute("SELECT 1 FROM users WHERE id=?", (payload.user_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Group or user not found")
        db.execute("INSERT OR IGNORE INTO user_groups(user_id,group_id) VALUES (?,?)", (payload.user_id, group_id))
    return {"group_id": group_id, "user_id": payload.user_id}


@router.get("/roles")
def list_roles(settings: Settings = Depends(get_settings), user=Depends(require_capability("role.manage"))):
    with connect(settings.sqlite_path) as db:
        rows = db.execute("SELECT id,name,immutable,capabilities_json FROM roles ORDER BY name").fetchall()
    return [{"id": row["id"], "name": row["name"], "immutable": bool(row["immutable"]), "capabilities": json.loads(row["capabilities_json"])} for row in rows]


@router.post("/roles", status_code=201)
def create_role(payload: RoleCreate, settings: Settings = Depends(get_settings), user=Depends(require_capability("role.manage"))):
    capabilities = _validate_capabilities(payload.capabilities)
    role_id = str(uuid.uuid4())
    with connect(settings.sqlite_path) as db:
        try:
            db.execute("INSERT INTO roles(id,name,immutable,capabilities_json) VALUES (?,?,0,?)", (role_id, payload.name.strip(), json.dumps(capabilities)))
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(status_code=409, detail="Role name already exists") from exc
            raise
    return {"id": role_id, "name": payload.name.strip(), "immutable": False, "capabilities": capabilities}


@router.patch("/roles/{role_id}")
def update_role(role_id: str, payload: RoleUpdate, settings: Settings = Depends(get_settings), user=Depends(require_capability("role.manage"))):
    with connect(settings.sqlite_path) as db:
        row = db.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Role not found")
        if row["immutable"]:
            raise HTTPException(status_code=409, detail="Built-in role is immutable")
        name = payload.name.strip() if payload.name is not None else row["name"]
        capabilities = _validate_capabilities(payload.capabilities if payload.capabilities is not None else json.loads(row["capabilities_json"]))
        db.execute("UPDATE roles SET name=?,capabilities_json=? WHERE id=?", (name, json.dumps(capabilities), role_id))
    return {"id": role_id, "name": name, "immutable": False, "capabilities": capabilities}


@router.post("/groups/{group_id}/roles")
def add_group_role(group_id: str, payload: dict, settings: Settings = Depends(get_settings), user=Depends(require_capability("group.manage"))):
    role_id = payload.get("role_id")
    if not role_id:
        raise HTTPException(status_code=422, detail="role_id is required")
    with connect(settings.sqlite_path) as db:
        if not db.execute("SELECT 1 FROM groups WHERE id=?", (group_id,)).fetchone() or not db.execute("SELECT 1 FROM roles WHERE id=?", (role_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Group or role not found")
        db.execute("INSERT OR IGNORE INTO group_roles(group_id,role_id) VALUES (?,?)", (group_id, role_id))
    return {"group_id": group_id, "role_id": role_id}
