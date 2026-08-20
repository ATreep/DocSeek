from fastapi.testclient import TestClient
from uuid import uuid4

from backend.app.api.mcp import TOOLS
from backend.app.config import Settings, get_settings
from backend.app.db import initialize
from backend.app.main import app
from backend.app.seed import seed_defaults


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


def test_mcp_endpoint_supports_streamable_http_protocol(tmp_path):
    settings = Settings(data_dir=tmp_path)
    initialize(settings.sqlite_path)
    seed_defaults(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            token = client.post(
                "/api/auth/login", json={"username": "admin", "password": "admin"}
            ).json()["token"]
            headers = {"Authorization": f"Bearer {token}"}
            project = client.post(
                "/api/projects",
                json={"name": f"MCP Protocol-{uuid4().hex}"},
                headers=headers,
            ).json()
            try:
                endpoint = client.post(
                    f"/api/projects/{project['id']}/mcp/open", headers=headers
                ).json()["endpoint"]
                protocol_headers = {
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                }

                initialized = client.post(
                    endpoint,
                    headers=protocol_headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "test-client", "version": "1.0"},
                        },
                    },
                )
                assert initialized.status_code == 200
                assert initialized.json()["result"] == {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "docseek-project-mcp",
                        "title": "DocSeek Project MCP",
                        "version": "0.1.0",
                    },
                    "instructions": "Use these tools to work with the currently open project.",
                }

                ready = client.post(
                    endpoint,
                    headers={**protocol_headers, "MCP-Protocol-Version": "2025-06-18"},
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                )
                assert ready.status_code == 202
                assert ready.content == b""

                listed = client.post(
                    endpoint,
                    headers={**protocol_headers, "MCP-Protocol-Version": "2025-06-18"},
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                )
                assert listed.status_code == 200
                assert {tool["name"] for tool in listed.json()["result"]["tools"]} == set(TOOLS)

                called = client.post(
                    endpoint,
                    headers={**protocol_headers, "MCP-Protocol-Version": "2025-06-18"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "list_properties", "arguments": {}},
                    },
                )
                assert called.status_code == 200
                result = called.json()["result"]
                assert result["isError"] is False
                assert result["structuredContent"] == {
                    "property_tree": {
                        "group_name": "",
                        "group_path": "",
                        "properties": [],
                        "groups": [],
                    }
                }
            finally:
                client.delete(f"/api/projects/{project['id']}", headers=headers)
    finally:
        app.dependency_overrides.pop(get_settings, None)
