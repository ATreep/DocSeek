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
import json


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

    assert {"stage_started_at", "stage_detail", "timings_json", "llm_response"} <= columns
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
        "progress": {},
    }


def test_failed_pipeline_records_the_original_llm_response(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    project_id, property_id, job_id = "llm-project", "llm-property", "llm-job"
    source = settings.projects_dir / project_id / "properties" / "manual.md"
    source.parent.mkdir(parents=True)
    source.write_text("Atlas manual", encoding="utf-8")
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES (?,?,?,?)",
            (project_id, "LLM failure", "now", "now"),
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
            "filename": "manual.md",
            "property_type": "markdown",
            "relative_path": "properties/manual.md",
            "definition": "",
            "status": "queued",
            "created_at": "now",
            "updated_at": "now",
        },
    )

    class RawModelFailure(ValueError):
        llm_response = '{"entities":[{"id":"错误 identifier"}]}'

    class FailingWorkflow:
        def invoke(self, _state):
            raise RawModelFailure("entity extraction provider returned an invalid entity id")

    monkeypatch.setattr(
        "backend.app.services.pipeline.build_workflow",
        lambda _store: FailingWorkflow(),
    )

    run_pipeline(
        settings,
        project_id,
        property_id,
        job_id,
        "manual.md",
        "markdown",
        source,
    )

    with connect(settings.sqlite_path) as db:
        job = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    assert job.get("llm_response") == RawModelFailure.llm_response


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


def test_retry_processing_resumes_a_failed_batch_after_completed_files(monkeypatch):
    scheduled = []

    def record_batch(*args):
        scheduled.append(args)

    monkeypatch.setattr("backend.app.api.status.run_batch_pipeline", record_batch)
    with TestClient(app) as client:
        token = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        ).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        project = client.post(
            "/api/projects",
            json={"name": f"Batch Retry-{uuid4().hex}"},
            headers=headers,
        ).json()
        items = []
        catalog = PropertyCatalog(get_settings())
        for index, property_id in enumerate(("done", "current", "pending"), start=1):
            path = (
                get_settings().projects_dir
                / project["id"]
                / "properties"
                / f"{property_id}.md"
            )
            path.write_text(property_id, encoding="utf-8")
            catalog.create(
                project["id"],
                {
                    "id": property_id,
                    "project_id": project["id"],
                    "filename": path.name,
                    "property_type": "markdown",
                    "relative_path": f"properties/{path.name}",
                    "definition": f"Definition {index}.",
                    "status": "queued" if property_id == "done" else "failed",
                    "created_at": "now",
                    "updated_at": "now",
                },
            )
            items.append(
                {
                    "property_id": property_id,
                    "filename": path.name,
                    "kind": "markdown",
                    "path": str(path),
                    "comment": "",
                    "definition": f"Definition {index}.",
                    "text": None,
                }
            )
        failed_job_id = f"failed-batch-{uuid4().hex}"
        with connect(get_settings().sqlite_path) as db:
            db.execute(
                "INSERT INTO jobs(id,project_id,stage,status,candidate_snapshot,heartbeat,input_json,progress_json) VALUES (?,?,?,?,?,?,?,?)",
                (
                    failed_job_id,
                    project["id"],
                    "graph-entity-extraction",
                    "failed",
                    "checkpoint-1",
                    "now",
                    json.dumps({"operation": "batch-add", "items": items}),
                    json.dumps(
                        {
                            "completed_property_ids": ["done"],
                            "candidate_snapshot": "checkpoint-1",
                            "directories": {
                                "done": "Product",
                                "current": "Product",
                                "pending": "Product",
                            },
                        }
                    ),
                ),
            )

        response = client.post(
            f"/api/projects/{project['id']}/processing/retry",
            json={"property_id": "current"},
            headers=headers,
        )

        assert response.status_code == 202
        assert response.json()["remaining"] == 2
        assert len(scheduled) == 1
        args = scheduled[0]
        assert args[1] == project["id"]
        assert [item["property_id"] for item in args[3]] == [
            "done",
            "current",
            "pending",
        ]
        assert args[4] == "checkpoint-1"
        assert args[5] == ["done"]


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
