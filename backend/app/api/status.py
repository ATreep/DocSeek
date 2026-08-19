import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..db import connect
from ..security import require_capability
from ..services.catalog import PropertyCatalog
from ..services.display_language import current_display_language, run_in_display_language
from ..services.pipeline import run_batch_pipeline, run_pipeline
from .projects import acquire_lock, is_locked, release_lock

router = APIRouter(prefix="/projects", tags=["processing"])


class RetryRequest(BaseModel):
    property_id: str = Field(min_length=1)


def _processing_payload(job) -> dict:
    payload = dict(job)
    payload.pop("input_json", None)
    raw_progress = payload.pop("progress_json", "{}") or "{}"
    raw_timings = payload.pop("timings_json", "{}") or "{}"
    try:
        timings = json.loads(raw_timings)
    except (TypeError, json.JSONDecodeError):
        timings = {}
    payload["timings"] = timings if isinstance(timings, dict) else {}
    payload["progress"] = _safe_json_object(raw_progress)
    return payload


def _safe_json_object(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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
    catalog = PropertyCatalog(settings)
    row = catalog.get(project_id, payload.property_id)
    if not row:
        raise HTTPException(status_code=404, detail="Property not found")
    with connect(settings.sqlite_path) as db:
        retryable_jobs = db.execute(
            "SELECT * FROM jobs WHERE project_id=? AND status IN ('failed','cancelled') ORDER BY heartbeat DESC",
            (project_id,),
        ).fetchall()
    failed_batch = None
    batch_input: dict = {}
    batch_progress: dict = {}
    for failed_job in retryable_jobs:
        try:
            candidate_input = json.loads(failed_job["input_json"] or "{}")
            candidate_progress = json.loads(failed_job["progress_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        items = candidate_input.get("items") if isinstance(candidate_input, dict) else None
        if (
            candidate_input.get("operation") == "batch-add"
            and isinstance(items, list)
            and any(item.get("property_id") == payload.property_id for item in items if isinstance(item, dict))
        ):
            failed_batch = failed_job
            batch_input = candidate_input
            batch_progress = candidate_progress if isinstance(candidate_progress, dict) else {}
            break
    job_id = str(uuid.uuid4())
    if not acquire_lock(settings, project_id, job_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    now = datetime.now(timezone.utc).isoformat()
    if failed_batch:
        completed_ids = {
            str(property_id)
            for property_id in batch_progress.get("completed_property_ids", [])
        }
        pending_items = [
            item
            for item in batch_input["items"]
            if item.get("property_id") not in completed_ids
        ]
        retry_targets = pending_items or [batch_input["items"][-1]]
        for item in retry_targets:
            try:
                catalog.update(
                    project_id,
                    item["property_id"],
                    {"status": "queued", "updated_at": now},
                )
            except KeyError:
                continue
        with connect(settings.sqlite_path) as db:
            db.execute(
                "INSERT INTO jobs(id,project_id,stage,status,candidate_snapshot,heartbeat,input_json,progress_json) VALUES (?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    project_id,
                    "queued",
                    "queued",
                    batch_progress.get("candidate_snapshot") or failed_batch["candidate_snapshot"],
                    now,
                    json.dumps(batch_input, ensure_ascii=False),
                    json.dumps(batch_progress, ensure_ascii=False),
                ),
            )
        background_tasks.add_task(
            run_in_display_language,
            current_display_language(),
            run_batch_pipeline,
            settings,
            project_id,
            job_id,
            batch_input["items"],
            batch_progress.get("candidate_snapshot") or failed_batch["candidate_snapshot"],
            sorted(completed_ids),
            batch_progress.get("directories"),
        )
        return {
            "status": "queued",
            "project_id": project_id,
            "property_id": retry_targets[0]["property_id"],
            "job_id": job_id,
            "remaining": len(pending_items),
        }

    catalog.update(project_id, payload.property_id, {"status": "queued", "updated_at": now})
    with connect(settings.sqlite_path) as db:
        db.execute("INSERT INTO jobs(id,project_id,stage,status,candidate_snapshot,heartbeat) VALUES (?,?,?,?,?,?)", (job_id, project_id, "queued", "queued", None, now))
    path = settings.projects_dir / project_id / row["relative_path"]
    background_tasks.add_task(
        run_in_display_language,
        current_display_language(),
        run_pipeline,
        settings,
        project_id,
        payload.property_id,
        job_id,
        row["filename"],
        row["property_type"],
        path,
        "",
        "retry",
    )
    return {"status": "queued", "project_id": project_id, "property_id": payload.property_id, "job_id": job_id}
