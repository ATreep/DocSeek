from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import Settings, get_settings
from ..services.graph_store import Neo4jGraphStore
from ..security import get_current_user
from .projects import get_project
from ..db import connect

router = APIRouter(prefix="/projects", tags=["graphs"])


@router.get("/{project_id}/graphs/{kind}")
def get_graph(project_id: str, kind: str, snapshot: str | None = Query(default=None), settings: Settings = Depends(get_settings), user=Depends(get_current_user)):
    if kind not in {"property", "entity"}:
        raise HTTPException(status_code=422, detail="Graph kind must be property or entity")
    capability = f"graph.{kind}.view"
    if capability not in user["capabilities"]:
        raise HTTPException(status_code=403, detail=f"Missing capability: {capability}")
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    snapshot_id = None
    if snapshot == "candidate":
        with connect(settings.sqlite_path) as db:
            job = db.execute("SELECT candidate_snapshot FROM jobs WHERE project_id=? AND candidate_snapshot IS NOT NULL ORDER BY heartbeat DESC LIMIT 1", (project_id,)).fetchone()
        snapshot_id = job["candidate_snapshot"] if job else None
    return Neo4jGraphStore(settings).graph(project_id, kind, snapshot_id=snapshot_id)
