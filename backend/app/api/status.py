import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..db import connect
from ..security import require_capability
from ..services.catalog import PropertyCatalog
from ..services.pipeline import run_pipeline
from .projects import acquire_lock, is_locked

router = APIRouter(prefix="/projects", tags=["processing"])


class RetryRequest(BaseModel):
    property_id: str = Field(min_length=1)


def _processing_payload(job) -> dict:
    payload = dict(job)
    raw_timings = payload.pop("timings_json", "{}") or "{}"
    try:
        timings = json.loads(raw_timings)
    except (TypeError, json.JSONDecodeError):
        timings = {}
    payload["timings"] = timings if isinstance(timings, dict) else {}
    return payload


@router.get("/{project_id}/processing")
def processing_status(project_id: str, settings: Settings = Depends(get_settings), user=Depends(require_capability("agent.status.view"))):
    with connect(settings.sqlite_path) as db:
        lock = db.execute("SELECT * FROM project_locks WHERE project_id=?", (project_id,)).fetchone()
        job = db.execute("SELECT * FROM jobs WHERE project_id=? ORDER BY heartbeat DESC LIMIT 1", (project_id,)).fetchone()
    if not lock and not job:
        return {"locked": False, "status": "idle", "stage": None}
    return {"locked": lock is not None, **(_processing_payload(job) if job else {"status": "idle", "stage": None})}


@router.post("/{project_id}/processing/cancel")
def cancel_processing(project_id: str, settings: Settings = Depends(get_settings), user=Depends(require_capability("agent.cancel"))):
    with connect(settings.sqlite_path) as db:
        lock = db.execute("SELECT * FROM project_locks WHERE project_id=?", (project_id,)).fetchone()
        if not lock:
            return {"status": "idle", "project_id": project_id}
        now = datetime.now(timezone.utc).isoformat()
        db.execute("UPDATE jobs SET status='cancelled', stage='cancelled', heartbeat=? WHERE id=?", (now, lock["job_id"]))
        db.execute("DELETE FROM project_locks WHERE project_id=?", (project_id,))
    return {"status": "cancelled", "project_id": project_id, "job_id": lock["job_id"]}


@router.post("/{project_id}/processing/retry", status_code=202)
def retry_processing(project_id: str, payload: RetryRequest, background_tasks: BackgroundTasks, settings: Settings = Depends(get_settings), user=Depends(require_capability("agent.retry"))):
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    row = PropertyCatalog(settings).get(project_id, payload.property_id)
    if not row:
        raise HTTPException(status_code=404, detail="Property not found")
    job_id = str(uuid.uuid4())
    if not acquire_lock(settings, project_id, job_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    now = datetime.now(timezone.utc).isoformat()
    PropertyCatalog(settings).update(project_id, payload.property_id, {"status": "queued", "updated_at": now})
    with connect(settings.sqlite_path) as db:
        db.execute("INSERT INTO jobs(id,project_id,stage,status,candidate_snapshot,heartbeat) VALUES (?,?,?,?,?,?)", (job_id, project_id, "queued", "queued", None, now))
    path = settings.projects_dir / project_id / row["relative_path"]
    background_tasks.add_task(run_pipeline, settings, project_id, payload.property_id, job_id, row["filename"], row["property_type"], path, "", "retry")
    return {"status": "queued", "project_id": project_id, "property_id": payload.property_id, "job_id": job_id}
