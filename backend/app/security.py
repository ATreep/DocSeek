import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, Header, HTTPException, status

from .config import Settings, get_settings
from .db import connect


CAPABILITIES = {
    "project.view", "project.create", "project.rename", "project.delete",
    "property.view", "property.upload", "property.replace", "property.edit",
    "property.delete", "property.rename", "property.move", "property.attribute.view",
    "property.attribute.edit", "graph.property.view", "graph.entity.view",
    "search.properties", "search.entities", "query.execute", "agent.status.view",
    "agent.retry", "agent.cancel", "system.config.view", "system.config.edit",
    "user.manage", "group.manage", "role.manage", "mcp.use", "mcp.configure",
}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"pbkdf2_sha256$120000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(actual.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def create_session(settings: Settings, user_id: str) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    with connect(settings.sqlite_path) as db:
        db.execute("INSERT INTO sessions(token, user_id, expires_at) VALUES (?, ?, ?)", (token, user_id, expiry.isoformat()))
    return token, expiry


def get_current_user(authorization: str | None = Header(default=None), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    token = authorization.split(" ", 1)[1].strip()
    with connect(settings.sqlite_path) as db:
        row = db.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires_at>?",
            (token, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
    if not row or row["disabled"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session")
    user = dict(row)
    user["capabilities"] = effective_capabilities(settings, user["id"])
    user.pop("password_hash", None)
    return user


def effective_capabilities(settings: Settings, user_id: str) -> set[str]:
    with connect(settings.sqlite_path) as db:
        rows = db.execute(
            """SELECT r.capabilities_json FROM roles r
               JOIN group_roles gr ON gr.role_id=r.id
               JOIN user_groups ug ON ug.group_id=gr.group_id
               WHERE ug.user_id=?""", (user_id,)
        ).fetchall()
    capabilities: set[str] = set()
    import json
    for row in rows:
        capabilities.update(json.loads(row["capabilities_json"]))
    return capabilities


def require_capability(capability: str):
    def dependency(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        if capability not in user["capabilities"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing capability: {capability}")
        return user

    return dependency
