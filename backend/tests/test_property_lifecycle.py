import io
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.main import app
from backend.tests.helpers import upload_and_confirm_property


def _headers(client: TestClient) -> dict[str, str]:
    token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _project(client: TestClient, headers: dict[str, str]) -> dict:
    return client.post("/api/projects", json={"name": f"Lifecycle-{uuid4().hex}"}, headers=headers).json()


def test_property_can_be_renamed_after_processing():
    with TestClient(app) as client:
        headers = _headers(client)
        project = _project(client, headers)
        created = upload_and_confirm_property(client, project["id"], headers, "notes.txt", b"notes", "text/plain")
        before = client.get(f"/api/projects/{project['id']}/properties", headers=headers).json()[0]
        response = client.patch(f"/api/projects/{project['id']}/properties/{created['property_id']}", json={"filename": "renamed.txt"}, headers=headers)
        assert response.status_code == 200
        assert response.json()["filename"] == "renamed.txt"
        settings = get_settings()
        assert (settings.projects_dir / project["id"] / response.json()["relative_path"]).read_bytes() == b"notes"
        assert not (settings.projects_dir / project["id"] / before["relative_path"]).exists()


def test_property_metadata_mutation_is_rejected_while_project_is_locked():
    with TestClient(app) as client:
        headers = _headers(client)
        project = _project(client, headers)
        created = upload_and_confirm_property(client, project["id"], headers, "notes.txt", b"notes", "text/plain")
        from backend.app.api.projects import acquire_lock
        assert acquire_lock(get_settings(), project["id"], "manual-metadata-lock")
        response = client.patch(f"/api/projects/{project['id']}/properties/{created['property_id']}", json={"definition": "blocked"}, headers=headers)
        assert response.status_code == 409


def test_property_can_be_replaced_and_raw_preview_requires_authentication():
    with TestClient(app) as client:
        headers = _headers(client)
        project = _project(client, headers)
        created = upload_and_confirm_property(client, project["id"], headers, "notes.txt", b"old", "text/plain")
        property_id = created["property_id"]
        replaced = client.put(f"/api/projects/{project['id']}/properties/{property_id}/content", files={"file": ("notes.txt", io.BytesIO(b"new"), "text/plain")}, headers=headers)
        assert replaced.status_code == 202
        raw = client.get(f"/api/projects/{project['id']}/properties/{property_id}/raw", headers=headers)
        assert raw.status_code == 200
        assert raw.content == b"new"
        assert client.get(f"/api/projects/{project['id']}/properties/{property_id}/raw").status_code == 401


def test_property_can_move_into_a_safe_directory_without_path_traversal():
    with TestClient(app) as client:
        headers = _headers(client)
        project = _project(client, headers)
        created = upload_and_confirm_property(client, project["id"], headers, "notes.txt", b"notes", "text/plain")
        moved = client.post(f"/api/projects/{project['id']}/properties/{created['property_id']}/move", json={"directory": "references"}, headers=headers)
        assert moved.status_code == 200
        assert moved.json()["relative_path"] == "properties/references/notes.txt"
        blocked = client.post(f"/api/projects/{project['id']}/properties/{created['property_id']}/move", json={"directory": "../outside"}, headers=headers)
        assert blocked.status_code == 422
