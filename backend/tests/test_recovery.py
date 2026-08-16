from datetime import datetime, timedelta, timezone

from backend.app.config import get_settings
from backend.app.db import connect, initialize
from backend.app.services.recovery import recover_stale_jobs


def test_stale_job_recovery_releases_lock_and_marks_job_failed(tmp_path):
    settings = get_settings().model_copy(update={"data_dir": tmp_path})
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with connect(settings.sqlite_path) as db:
        db.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES ('p','Recovery','now','now')")
        db.execute("INSERT INTO jobs(id,project_id,stage,status,heartbeat) VALUES ('j','p','dg-agent','running',?)", (old,))
        db.execute("INSERT INTO project_locks(project_id,job_id,acquired_at) VALUES ('p','j',?)", (old,))
    recovered = recover_stale_jobs(settings, stale_after_seconds=60)
    assert recovered == ["j"]
    with connect(settings.sqlite_path) as db:
        job = db.execute("SELECT status,error FROM jobs WHERE id='j'").fetchone()
        lock = db.execute("SELECT 1 FROM project_locks WHERE project_id='p'").fetchone()
    assert job["status"] == "failed"
    assert "stale" in job["error"].lower()
    assert lock is None


def test_pipeline_rejects_missing_original_and_releases_lock(tmp_path):
    settings = get_settings().model_copy(update={"data_dir": tmp_path})
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    with connect(settings.sqlite_path) as db:
        db.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES ('p','Failure','now','now')")
        db.execute("INSERT INTO jobs(id,project_id,stage,status,heartbeat) VALUES ('j','p','queued','queued','now')")
    from backend.app.services.catalog import PropertyCatalog
    from backend.app.services.pipeline import run_pipeline
    PropertyCatalog(settings).create("p", {"id": "prop", "project_id": "p", "filename": "missing.txt", "property_type": "text", "relative_path": "properties/missing.txt", "status": "queued"})
    from backend.app.api.projects import acquire_lock
    assert acquire_lock(settings, "p", "j")
    run_pipeline(settings, "p", "prop", "j", "missing.txt", "text", settings.projects_dir / "p" / "properties/missing.txt")
    with connect(settings.sqlite_path) as db:
        job = db.execute("SELECT status FROM jobs WHERE id='j'").fetchone()
        lock = db.execute("SELECT 1 FROM project_locks WHERE project_id='p'").fetchone()
    assert job["status"] == "failed"
    assert lock is None
