from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..db import connect
from ..security import create_session, get_current_user, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


@router.post("/login")
def login(payload: LoginRequest, settings: Settings = Depends(get_settings)):
    with connect(settings.sqlite_path) as db:
        user = db.execute("SELECT * FROM users WHERE username=?", (payload.username.strip(),)).fetchone()
    if not user or user["disabled"] or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token, expiry = create_session(settings, user["id"])
    return {"token": token, "expires_at": expiry.isoformat(), "user": {"id": user["id"], "username": user["username"]}}


@router.get("/me")
def me(user=Depends(get_current_user)):
    return {"id": user["id"], "username": user["username"], "capabilities": sorted(user["capabilities"])}


@router.post("/logout")
def logout(user=Depends(get_current_user), settings: Settings = Depends(get_settings), authorization: str | None = Header(default=None)):
    if authorization and authorization.lower().startswith("bearer "):
        with connect(settings.sqlite_path) as db:
            db.execute("DELETE FROM sessions WHERE token=?", (authorization.split(" ", 1)[1].strip(),))
    from .mcp import close_active_for_user
    close_active_for_user(user["id"])
    return {"status": "logged_out", "username": user["username"]}
