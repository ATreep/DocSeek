from fastapi.testclient import TestClient
from uuid import uuid4

from backend.app.main import app


def test_admin_can_login_create_and_rename_project():
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        created = client.post("/api/projects", json={"name": f"Research-{uuid4().hex}"}, headers=headers)
        assert created.status_code == 201
        project = created.json()
        renamed = client.patch(f"/api/projects/{project['id']}", json={"name": f"Research 2026-{uuid4().hex}"}, headers=headers)
        assert renamed.status_code == 200
        assert renamed.json()["name"].startswith("Research 2026-")


def test_duplicate_project_names_are_rejected():
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        name = f"Duplicate Name-{uuid4().hex}"
        assert client.post("/api/projects", json={"name": name}, headers=headers).status_code == 201
        assert client.post("/api/projects", json={"name": name}, headers=headers).status_code == 409
