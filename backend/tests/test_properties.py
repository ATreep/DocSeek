import io
import json
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.db import connect
from backend.app.main import app
from backend.app.services.agents import DefinitionResult
from backend.app.services.catalog import PropertyCatalog
from backend.tests.helpers import upload_and_confirm_property


def _auth(client):
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_image_property_is_accepted_and_processed_without_entity_input():
    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post("/api/projects", json={"name": f"Images-{uuid4().hex}"}, headers=headers).json()
        upload_and_confirm_property(client, project["id"], headers, "diagram.png", b"fake image", "image/png")
        prop = client.get(f"/api/projects/{project['id']}/properties", headers=headers).json()[0]
        assert prop["property_type"] == "image"


def test_upload_returns_filename_suggestion_without_storing_it_as_property_metadata(monkeypatch):
    import backend.app.api.properties as properties_api

    monkeypatch.setattr(
        properties_api,
        "generate_property_metadata",
        lambda *_args, **_kwargs: DefinitionResult(
            "A release plan for Atlas.",
            "atlas-release-plan.md",
        ),
        raising=False,
    )
    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post(
            "/api/projects",
            json={"name": f"Transient Suggestion-{uuid4().hex}"},
            headers=headers,
        ).json()

        uploaded = client.post(
            f"/api/projects/{project['id']}/properties",
            files={"file": ("notes.md", io.BytesIO(b"Release planning"), "text/markdown")},
            headers=headers,
        )

        assert uploaded.status_code == 202
        assert uploaded.json()["status"] == "awaiting_confirmation"
        assert uploaded.json()["suggested_filename"] == "atlas-release-plan.md"
        staged_record = (
            get_settings().projects_dir
            / project["id"]
            / "imports"
            / uploaded.json()["import_id"]
            / "import.json"
        ).read_text(encoding="utf-8")
        assert "suggested_filename" not in staged_record
        assert client.get(
            f"/api/projects/{project['id']}/properties",
            headers=headers,
        ).json() == []

        confirmed = client.post(
            f"/api/projects/{project['id']}/property-imports/{uploaded.json()['import_id']}/confirm",
            json={"filename": "atlas-release-plan.md"},
            headers=headers,
        )
        assert confirmed.status_code == 202
        property_id = confirmed.json()["property_id"]
        prop = client.get(
            f"/api/projects/{project['id']}/properties/{property_id}",
            headers=headers,
        ).json()
        attribute = client.get(
            f"/api/projects/{project['id']}/properties/{property_id}/attribute",
            headers=headers,
        ).json()
        assert "filename_suggestion" not in prop
        assert "filename_suggestion" not in attribute
        catalog_path = (
            get_settings().projects_dir
            / project["id"]
            / "jobs"
            / "property-catalog.json"
        )
        assert "filename_suggestion" not in catalog_path.read_text(encoding="utf-8")


def test_upload_does_not_create_a_job_or_start_graph_generation_until_confirmed(monkeypatch):
    import backend.app.api.properties as properties_api

    scheduled = []
    monkeypatch.setattr(
        properties_api,
        "generate_property_metadata",
        lambda *_args, **_kwargs: DefinitionResult(
            "A release plan for Atlas.",
            "atlas-release-plan.md",
        ),
    )
    monkeypatch.setattr(
        properties_api,
        "_schedule",
        lambda _background_tasks, function, *args: scheduled.append((function, args)),
    )

    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post(
            "/api/projects",
            json={"name": f"Staged Import-{uuid4().hex}"},
            headers=headers,
        ).json()

        staged = client.post(
            f"/api/projects/{project['id']}/properties",
            files={"file": ("notes.md", io.BytesIO(b"Release planning"), "text/markdown")},
            headers=headers,
        )

        assert staged.status_code == 202
        payload = staged.json()
        assert payload["status"] == "awaiting_confirmation"
        assert payload["original_filename"] == "notes.md"
        assert scheduled == []
        assert client.get(
            f"/api/projects/{project['id']}/properties",
            headers=headers,
        ).json() == []
        with connect(get_settings().sqlite_path) as db:
            assert db.execute(
                "SELECT COUNT(*) FROM jobs WHERE project_id=?",
                (project["id"],),
            ).fetchone()[0] == 0
            assert db.execute(
                "SELECT COUNT(*) FROM project_locks WHERE project_id=?",
                (project["id"],),
            ).fetchone()[0] == 0

        confirmed = client.post(
            f"/api/projects/{project['id']}/property-imports/{payload['import_id']}/confirm",
            json={"filename": "atlas-launch.md"},
            headers=headers,
        )

        assert confirmed.status_code == 202
        assert len(scheduled) == 1
        props = client.get(
            f"/api/projects/{project['id']}/properties",
            headers=headers,
        ).json()
        assert props[0]["filename"] == "atlas-launch.md"
        assert not (
            get_settings().projects_dir
            / project["id"]
            / "imports"
            / payload["import_id"]
        ).exists()


def test_cancelling_a_staged_import_removes_it_without_creating_a_property(monkeypatch):
    import backend.app.api.properties as properties_api

    scheduled = []
    monkeypatch.setattr(
        properties_api,
        "generate_property_metadata",
        lambda *_args, **_kwargs: DefinitionResult(
            "A release plan for Atlas.",
            "atlas-release-plan.md",
        ),
    )
    monkeypatch.setattr(
        properties_api,
        "_schedule",
        lambda _background_tasks, function, *args: scheduled.append((function, args)),
    )

    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post(
            "/api/projects",
            json={"name": f"Cancelled Import-{uuid4().hex}"},
            headers=headers,
        ).json()
        staged = client.post(
            f"/api/projects/{project['id']}/properties",
            files={"file": ("notes.md", io.BytesIO(b"Release planning"), "text/markdown")},
            headers=headers,
        ).json()
        import_dir = (
            get_settings().projects_dir
            / project["id"]
            / "imports"
            / staged["import_id"]
        )
        assert import_dir.is_dir()

        cancelled = client.delete(
            f"/api/projects/{project['id']}/property-imports/{staged['import_id']}",
            headers=headers,
        )

        assert cancelled.status_code == 204
        assert not import_dir.exists()
        assert scheduled == []
        assert client.get(
            f"/api/projects/{project['id']}/properties",
            headers=headers,
        ).json() == []


def test_regroup_sends_complete_tree_and_definitions_to_ga_agent(monkeypatch):
    import backend.app.api.properties as properties_api

    seen = {}

    class RecordingGAAgent:
        def __init__(self, settings):
            seen["settings"] = settings

        def rearrange_tree(self, tree_context, revision_prompt):
            seen["tree_context"] = tree_context
            seen["revision_prompt"] = revision_prompt
            return {
                "manual": "Product/Atlas/Guides",
                "release": "Product/Atlas/Releases",
            }

    monkeypatch.setattr(properties_api, "GAAgent", RecordingGAAgent, raising=False)

    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post(
            "/api/projects",
            json={"name": f"Regroup-{uuid4().hex}"},
            headers=headers,
        ).json()
        catalog = PropertyCatalog(get_settings())
        property_root = get_settings().projects_dir / project["id"] / "properties"
        (property_root / "atlas-manual.md").write_text("Atlas manual", encoding="utf-8")
        (property_root / "atlas-release.md").write_text("Atlas release", encoding="utf-8")
        catalog.create(project["id"], {
            "id": "manual",
            "project_id": project["id"],
            "filename": "atlas-manual.md",
            "property_type": "markdown",
            "relative_path": "properties/atlas-manual.md",
            "definition": "A user manual for Atlas.",
            "status": "active",
            "created_at": "now",
            "updated_at": "now",
        })
        catalog.create(project["id"], {
            "id": "release",
            "project_id": project["id"],
            "filename": "atlas-release.md",
            "property_type": "markdown",
            "relative_path": "properties/atlas-release.md",
            "definition": "The Atlas release checklist.",
            "status": "active",
            "created_at": "now",
            "updated_at": "now",
        })

        regrouped = client.post(
            f"/api/projects/{project['id']}/properties/regroup",
            json={
                "revision_prompt": "Put Atlas guides and release documents in separate subgroups."
            },
            headers=headers,
        )

        assert regrouped.status_code == 200
        assert seen["revision_prompt"] == (
            "Put Atlas guides and release documents in separate subgroups."
        )
        serialized_tree = json.dumps(seen["tree_context"], ensure_ascii=False)
        assert "atlas-manual.md" in serialized_tree
        assert "A user manual for Atlas." in serialized_tree
        assert "atlas-release.md" in serialized_tree
        assert "The Atlas release checklist." in serialized_tree
        rows = {row["id"]: row for row in regrouped.json()["properties"]}
        assert rows["manual"]["relative_path"] == (
            "properties/Product/Atlas/Guides/atlas-manual.md"
        )
        assert rows["release"]["relative_path"] == (
            "properties/Product/Atlas/Releases/atlas-release.md"
        )
        assert (
            get_settings().projects_dir
            / project["id"]
            / rows["manual"]["relative_path"]
        ).is_file()


def test_property_mutation_is_rejected_while_project_is_locked():
    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post("/api/projects", json={"name": f"Locked-{uuid4().hex}"}, headers=headers).json()
        upload_and_confirm_property(client, project["id"], headers, "a.txt", b"Alpha", "text/plain")
        # BackgroundTasks are completed by TestClient before returning, so explicitly exercise the durable lock contract below.
        from backend.app.api.projects import acquire_lock
        from backend.app.config import get_settings
        assert acquire_lock(get_settings(), project["id"], "manual-job")
        blocked = client.post(f"/api/projects/{project['id']}/properties", files={"file": ("b.txt", io.BytesIO(b"Beta"), "text/plain")}, headers=headers)
        assert blocked.status_code == 409


def test_remove_property_is_permanent_after_candidate_activation():
    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post("/api/projects", json={"name": f"Remove-{uuid4().hex}"}, headers=headers).json()
        created = upload_and_confirm_property(client, project["id"], headers, "remove.txt", b"Remove me", "text/plain")
        property_id = created["property_id"]
        removed = client.delete(f"/api/projects/{project['id']}/properties/{property_id}", headers=headers)
        assert removed.status_code == 202
        assert client.get(f"/api/projects/{project['id']}/properties", headers=headers).json() == []


def test_completed_job_records_candidate_snapshot_and_provider_routes():
    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post("/api/projects", json={"name": f"Job Record-{uuid4().hex}"}, headers=headers).json()
        created = upload_and_confirm_property(client, project["id"], headers, "record.txt", b"DocSeek records processing state", "text/plain")

        with connect(get_settings().sqlite_path) as db:
            job = db.execute("SELECT status,candidate_snapshot,active_snapshot,routes_json FROM jobs WHERE id=?", (created["job_id"],)).fetchone()

        assert job["status"] == "completed"
        assert job["candidate_snapshot"] == job["active_snapshot"]
        routes = json.loads(job["routes_json"])
        assert set(routes) == {"dg_agent_route", "ga_agent_route", "pgb_agent_route", "entity_agent_route", "ai_query_route", "shared_embedding_route"}
        assert all("model" in route and "provider_type" in route for route in routes.values())
