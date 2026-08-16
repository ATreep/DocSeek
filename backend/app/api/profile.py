import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..db import connect
from ..security import get_current_user, hash_password, verify_password

router = APIRouter(prefix="/profile", tags=["profile"])


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=200)


@router.get("")
def profile(settings: Settings = Depends(get_settings), user=Depends(get_current_user)):
    with connect(settings.sqlite_path) as db:
        groups = db.execute("SELECT g.id,g.name FROM groups g JOIN user_groups ug ON ug.group_id=g.id WHERE ug.user_id=? ORDER BY g.name", (user["id"],)).fetchall()
        roles = db.execute("SELECT DISTINCT r.id,r.name FROM roles r JOIN group_roles gr ON gr.role_id=r.id JOIN user_groups ug ON ug.group_id=gr.group_id WHERE ug.user_id=? ORDER BY r.name", (user["id"],)).fetchall()
        preferences = db.execute("SELECT preferences_json FROM user_preferences WHERE user_id=?", (user["id"],)).fetchone()
    return {"id": user["id"], "username": user["username"], "disabled": bool(user["disabled"]), "groups": [dict(row) for row in groups], "roles": [dict(row) for row in roles], "capabilities": sorted(user["capabilities"]), "preferences": json.loads(preferences["preferences_json"]) if preferences else {}}


@router.post("/password")
def change_password(payload: PasswordChange, settings: Settings = Depends(get_settings), user=Depends(get_current_user)):
    with connect(settings.sqlite_path) as db:
        row = db.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not row or not verify_password(payload.current_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(payload.new_password), user["id"]))
    return {"status": "password_changed"}


@router.patch("/preferences")
def update_preferences(payload: dict, settings: Settings = Depends(get_settings), user=Depends(get_current_user)):
    now = datetime.now(timezone.utc).isoformat()
    with connect(settings.sqlite_path) as db:
        db.execute("INSERT INTO user_preferences(user_id,preferences_json,updated_at) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET preferences_json=excluded.preferences_json,updated_at=excluded.updated_at", (user["id"], json.dumps(payload), now))
    return {"preferences": payload}
