from fastapi.testclient import TestClient
from uuid import uuid4

from backend.app.main import app


def test_mcp_requires_manual_open_and_opening_another_project_replaces_endpoint():
    with TestClient(app) as client:
        token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        suffix = uuid4().hex
        one = client.post("/api/projects", json={"name": f"MCP One-{suffix}"}, headers=headers).json()
        two = client.post("/api/projects", json={"name": f"MCP Two-{suffix}"}, headers=headers).json()
        assert client.get(f"/api/projects/{one['id']}/mcp", headers=headers).json()["open"] is False
        assert client.post(f"/api/projects/{one['id']}/mcp/open", headers=headers).status_code == 200
        assert client.post(f"/api/projects/{two['id']}/mcp/open", headers=headers).json()["project_id"] == two["id"]
        assert client.get(f"/api/projects/{one['id']}/mcp", headers=headers).json()["open"] is False


def test_mcp_no_longer_exposes_ask_ai_query():
    with TestClient(app) as client:
        token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        project = client.post("/api/projects", json={"name": f"MCP Query-{uuid4().hex}"}, headers=headers).json()
        client.post(f"/api/projects/{project['id']}/mcp/open", headers=headers).raise_for_status()
        response = client.post(f"/api/projects/{project['id']}/mcp/call/ask_ai_query", headers=headers, json={"query": "What is ready?"})
        assert response.status_code == 404
        assert response.json()["detail"] == "Unknown MCP tool"
