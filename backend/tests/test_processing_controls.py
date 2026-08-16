from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.api.projects import acquire_lock
from backend.app.config import Settings, get_settings
from backend.app.db import connect, initialize
from backend.app.main import app
from backend.app.services.catalog import PropertyCatalog
from backend.app.services.graph_store import GraphSnapshot, Neo4jGraphStore
from backend.app.services.pipeline import run_pipeline
from backend.app.api.status import _processing_payload
from backend.tests.helpers import upload_and_confirm_property


def test_cancel_processing_releases_lock_and_marks_job_cancelled():
    with TestClient(app) as client:
        token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        project = client.post("/api/projects", json={"name": f"Cancel-{uuid4().hex}"}, headers=headers).json()
        job_id = f"manual-cancel-{uuid4().hex}"
        with connect(get_settings().sqlite_path) as db:
            db.execute("INSERT INTO jobs(id,project_id,stage,status,heartbeat) VALUES (?,?,?,?,?)", (job_id, project["id"], "dg-agent", "running", "now"))
        assert acquire_lock(get_settings(), project["id"], job_id)
        response = client.post(f"/api/projects/{project['id']}/processing/cancel", headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        status = client.get(f"/api/projects/{project['id']}/processing", headers=headers).json()
        assert status["locked"] is False


def test_job_schema_and_processing_payload_include_stage_diagnostics(tmp_path):
    settings = Settings(data_dir=tmp_path)
    initialize(settings.sqlite_path)
    with connect(settings.sqlite_path) as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}

    assert {"stage_started_at", "stage_detail", "timings_json"} <= columns
    assert _processing_payload(
        {
            "stage": "graph-entity-extraction",
            "stage_detail": "Analyzing 12 documents",
            "timings_json": '{"graph-property-read":0.42}',
        }
    ) == {
        "stage": "graph-entity-extraction",
        "stage_detail": "Analyzing 12 documents",
        "timings": {"graph-property-read": 0.42},
    }


def test_retry_processing_requeues_a_failed_property_job():
    with TestClient(app) as client:
        token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        project = client.post("/api/projects", json={"name": f"Retry-{uuid4().hex}"}, headers=headers).json()
        created = upload_and_confirm_property(client, project["id"], headers, "retry.txt", b"retry", "text/plain")
        with connect(get_settings().sqlite_path) as db:
            db.execute("UPDATE jobs SET status='failed', error='test failure' WHERE id=?", (created["job_id"],))
        response = client.post(f"/api/projects/{project['id']}/processing/retry", json={"property_id": created["property_id"]}, headers=headers)
        assert response.status_code == 202
        assert response.json()["status"] == "queued"


def test_failed_pipeline_records_a_developer_traceback(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    project_id, property_id, job_id = "failed-project", "failed-property", "failed-job"
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES (?,?,?,?)",
            (project_id, "Failed", "now", "now"),
        )
        db.execute(
            "INSERT INTO jobs(id,project_id,stage,status,heartbeat) VALUES (?,?,?,?,?)",
            (job_id, project_id, "queued", "queued", "now"),
        )
    PropertyCatalog(settings).create(
        project_id,
        {
            "id": property_id,
            "project_id": project_id,
            "filename": "missing.md",
            "property_type": "markdown",
            "relative_path": "properties/missing.md",
            "definition": "",
            "status": "queued",
            "created_at": "now",
            "updated_at": "now",
        },
    )

    run_pipeline(
        settings,
        project_id,
        property_id,
        job_id,
        "missing.md",
        "markdown",
        tmp_path / "missing.md",
    )

    with connect(settings.sqlite_path) as db:
        job = db.execute(
            "SELECT error,error_detail FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
    assert "missing.md" in job["error"]
    assert "Traceback (most recent call last)" in job["error_detail"]
    assert "FileNotFoundError" in job["error_detail"]


def test_cancelled_job_cannot_activate_a_candidate_snapshot(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    project_id, property_id, job_id = "cancel-project", "cancel-property", "cancel-job"
    project_root = settings.projects_dir / project_id
    (project_root / "properties").mkdir(parents=True)
    path = project_root / "properties" / "notes.md"
    path.write_text("Neo4j DocSeek", encoding="utf-8")
    PropertyCatalog(settings).create(project_id, {"id": property_id, "project_id": project_id, "filename": "notes.md", "property_type": "markdown", "relative_path": "properties/notes.md", "definition": "old", "filename_suggestion": "old.md", "status": "active", "created_at": "now", "updated_at": "now"})
    with connect(settings.sqlite_path) as db:
        db.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES (?,?,?,?)", (project_id, "Cancelled", "now", "now"))
        db.execute("INSERT INTO jobs(id,project_id,stage,status,heartbeat) VALUES (?,?,?,?,?)", (job_id, project_id, "cancelled", "cancelled", "now"))
    store = Neo4jGraphStore(settings)
    store.local.write_snapshot(GraphSnapshot(project_id, "active", [{"id": property_id, "definition": "old"}], [], [], []))
    store.local.activate(project_id, "active")
    store.close()
    run_pipeline(settings, project_id, property_id, job_id, "notes.md", "markdown", path)
    assert Neo4jGraphStore(settings).local.read(project_id).properties[0]["definition"] == "old"
    with connect(settings.sqlite_path) as db:
        assert db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()["status"] == "cancelled"
