from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.main import app


def test_search_results_are_filtered_by_property_and_entity_capabilities():
    with TestClient(app) as client:
        admin_token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
        admin = {"Authorization": f"Bearer {admin_token}"}
        role = client.post("/api/admin/roles", json={"name": f"Property Search-{uuid4().hex}", "capabilities": ["project.view", "search.properties"]}, headers=admin).json()
        group = client.post("/api/admin/groups", json={"name": f"Search Group-{uuid4().hex}"}, headers=admin).json()
        user = client.post("/api/admin/users", json={"username": f"search-{uuid4().hex}", "password": "search-pass"}, headers=admin).json()
        client.post(f"/api/admin/groups/{group['id']}/members", json={"user_id": user["id"]}, headers=admin)
        client.post(f"/api/admin/groups/{group['id']}/roles", json={"role_id": role["id"]}, headers=admin)
        user_token = client.post("/api/auth/login", json={"username": user["username"], "password": "search-pass"}).json()["token"]
        user_headers = {"Authorization": f"Bearer {user_token}"}
        project = client.post("/api/projects", json={"name": f"Search Permission-{uuid4().hex}"}, headers=admin).json()
        combined = client.post(f"/api/projects/{project['id']}/search", json={"query": "Neo4j"}, headers=user_headers)
        assert combined.status_code == 200
        assert "entities" not in combined.json()
        assert client.post(f"/api/projects/{project['id']}/search/entities", json={"query": "Neo4j"}, headers=user_headers).status_code == 403


def test_entity_graph_uses_the_entity_graph_capability():
    with TestClient(app) as client:
        admin_token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
        admin = {"Authorization": f"Bearer {admin_token}"}
        role = client.post("/api/admin/roles", json={"name": f"Entity Graph-{uuid4().hex}", "capabilities": ["graph.entity.view"]}, headers=admin).json()
        group = client.post("/api/admin/groups", json={"name": f"Entity Graph Group-{uuid4().hex}"}, headers=admin).json()
        user = client.post("/api/admin/users", json={"username": f"entity-graph-{uuid4().hex}", "password": "entity-pass"}, headers=admin).json()
        client.post(f"/api/admin/groups/{group['id']}/members", json={"user_id": user["id"]}, headers=admin)
        client.post(f"/api/admin/groups/{group['id']}/roles", json={"role_id": role["id"]}, headers=admin)
        token = client.post("/api/auth/login", json={"username": user["username"], "password": "entity-pass"}).json()["token"]
        project = client.post("/api/projects", json={"name": f"Entity Graph Project-{uuid4().hex}"}, headers=admin).json()

        headers = {"Authorization": f"Bearer {token}"}
        assert client.get(f"/api/projects/{project['id']}/graphs/entity", headers=headers).status_code == 200
        assert client.get(f"/api/projects/{project['id']}/graphs/property", headers=headers).status_code == 403
