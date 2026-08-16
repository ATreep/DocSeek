from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.tests.helpers import upload_and_confirm_property


def test_project_mcp_endpoint_exposes_documented_tools_without_second_authentication():
    with TestClient(app) as client:
        token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        project = client.post("/api/projects", json={"name": f"MCP Contract-{uuid4().hex}"}, headers=headers).json()
        upload_and_confirm_property(client, project["id"], headers, "mcp.txt", b"Neo4j DocSeek", "text/plain")
        opened = client.post(f"/api/projects/{project['id']}/mcp/open", headers=headers)
        endpoint = opened.json()["endpoint"]
        assert endpoint.startswith("/api/mcp/")
        listed = client.post(f"{endpoint}/list_properties", json={})
        assert listed.status_code == 200
        assert listed.json()["properties"][0]["filename"] == "mcp.txt"
        status = client.post(f"{endpoint}/get_processing_status", json={})
        assert status.status_code == 200
        assert "locked" in status.json()
        searched = client.post(f"{endpoint}/search_properties", json={"query": "Neo4j"})
        assert searched.status_code == 200


def test_mcp_endpoint_closes_when_another_project_is_opened():
    with TestClient(app) as client:
        token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        first = client.post("/api/projects", json={"name": f"MCP Old-{uuid4().hex}"}, headers=headers).json()
        second = client.post("/api/projects", json={"name": f"MCP New-{uuid4().hex}"}, headers=headers).json()
        endpoint = client.post(f"/api/projects/{first['id']}/mcp/open", headers=headers).json()["endpoint"]
        client.post(f"/api/projects/{second['id']}/mcp/open", headers=headers)
        assert client.post(f"{endpoint}/list_properties", json={}).status_code == 409


def test_mcp_mutation_and_query_tools_share_project_pipeline_contract():
    with TestClient(app) as client:
        token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        project = client.post("/api/projects", json={"name": f"MCP Tools-{uuid4().hex}"}, headers=headers).json()
        endpoint = client.post(f"/api/projects/{project['id']}/mcp/open", headers=headers).json()["endpoint"]
        added = client.post(f"{endpoint}/add_property", json={"filename": "tool.txt", "content": "Neo4j DocSeek", "content_type": "text/plain"})
        assert added.status_code == 200
        property_id = added.json()["property_id"]
        assert client.post(f"{endpoint}/get_property", json={"property_id": property_id}).json()["filename"] == "tool.txt"
        attribute = client.post(f"{endpoint}/get_property_attribute", json={"property_id": property_id})
        assert attribute.status_code == 200
        assert "filename_suggestion" not in attribute.json()
        replaced = client.post(f"{endpoint}/replace_property", json={"property_id": property_id, "filename": "tool.txt", "content": "Neo4j GraphRAG DocSeek"})
        assert replaced.status_code == 200
        assert client.post(f"{endpoint}/get_property_graph", json={}).status_code == 200
        entities = client.post(f"{endpoint}/list_entities", json={}).json()["entities"]
        if entities:
            assert client.post(f"{endpoint}/get_entity", json={"entity_id": entities[0]["id"]}).status_code == 200
        assert client.post(f"{endpoint}/ask_ai_query", json={"query": "What is here?"}).status_code == 200
        removed = client.post(f"{endpoint}/remove_property", json={"property_id": property_id})
        assert removed.status_code == 200


def test_project_delete_closes_mcp_and_removes_local_graph_snapshot():
    with TestClient(app) as client:
        token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        project = client.post("/api/projects", json={"name": f"MCP Delete-{uuid4().hex}"}, headers=headers).json()
        endpoint = client.post(f"/api/projects/{project['id']}/mcp/open", headers=headers).json()["endpoint"]
        from backend.app.config import get_settings
        graph_path = get_settings().data_dir / "graph-fallback" / f"{project['id']}.json"
        graph_path.parent.mkdir(parents=True, exist_ok=True)
        graph_path.write_text("{}", encoding="utf-8")
        assert client.delete(f"/api/projects/{project['id']}", headers=headers).status_code == 200
        assert client.post(f"{endpoint}/list_properties", json={}).status_code == 409
        assert not graph_path.exists()
