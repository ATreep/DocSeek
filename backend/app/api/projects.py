import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..db import connect
from ..security import get_current_user, require_capability

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def _project(row):
    return dict(row) if row else None


def get_project(settings: Settings, project_id: str):
    with connect(settings.sqlite_path) as db:
        return db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()


def is_locked(settings: Settings, project_id: str) -> bool:
    with connect(settings.sqlite_path) as db:
        return db.execute("SELECT 1 FROM project_locks WHERE project_id=?", (project_id,)).fetchone() is not None


def acquire_lock(settings: Settings, project_id: str, job_id: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with connect(settings.sqlite_path) as db:
        try:
            db.execute("INSERT INTO project_locks(project_id, job_id, acquired_at) VALUES (?, ?, ?)", (project_id, job_id, now))
        except Exception:
            return False
    return True


def release_lock(settings: Settings, project_id: str, job_id: str | None = None) -> None:
    with connect(settings.sqlite_path) as db:
        if job_id:
            db.execute("DELETE FROM project_locks WHERE project_id=? AND job_id=?", (project_id, job_id))
        else:
            db.execute("DELETE FROM project_locks WHERE project_id=?", (project_id,))


@router.get("")
def list_projects(settings: Settings = Depends(get_settings), user=Depends(require_capability("project.view"))):
    with connect(settings.sqlite_path) as db:
        rows = db.execute(
            "SELECT p.*, CASE WHEN l.project_id IS NULL THEN 0 ELSE 1 END AS processing FROM projects p LEFT JOIN project_locks l ON p.id=l.project_id ORDER BY p.name"
        ).fetchall()
    return [_project(row) for row in rows]


@router.post("", status_code=201)
def create_project(payload: ProjectRequest, settings: Settings = Depends(get_settings), user=Depends(require_capability("project.create"))):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Project name is required")
    project_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with connect(settings.sqlite_path) as db:
        try:
            db.execute("INSERT INTO projects(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)", (project_id, name, now, now))
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(status_code=409, detail="Project name already exists") from exc
            raise
    (settings.projects_dir / project_id / "properties").mkdir(parents=True, exist_ok=True)
    (settings.projects_dir / project_id / "extracted-text").mkdir(parents=True, exist_ok=True)
    (settings.projects_dir / project_id / "jobs").mkdir(parents=True, exist_ok=True)
    return {"id": project_id, "name": name, "processing": False, "created_at": now, "updated_at": now}


@router.patch("/{project_id}")
def rename_project(project_id: str, payload: ProjectRequest, settings: Settings = Depends(get_settings), user=Depends(require_capability("project.rename"))):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Project name is required")
    now = datetime.now(timezone.utc).isoformat()
    with connect(settings.sqlite_path) as db:
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Project not found")
        try:
            db.execute("UPDATE projects SET name=?, updated_at=? WHERE id=?", (name, now, project_id))
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(status_code=409, detail="Project name already exists") from exc
            raise
        row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return _project(row)


@router.delete("/{project_id}")
def delete_project(project_id: str, settings: Settings = Depends(get_settings), user=Depends(require_capability("project.delete"))):
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    with connect(settings.sqlite_path) as db:
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Project not found")
        db.execute("DELETE FROM projects WHERE id=?", (project_id,))
    from ..services.graph_store import Neo4jGraphStore
    from .mcp import close_active_for_project
    graph_store = Neo4jGraphStore(settings)
    try:
        graph_store.delete_project(project_id)
    finally:
        graph_store.close()
    close_active_for_project(project_id)
    import shutil
    shutil.rmtree(settings.projects_dir / project_id, ignore_errors=True)
    return {"status": "deleted", "id": project_id}
