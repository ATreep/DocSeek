from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.main import app
from backend.app.services.catalog import PropertyCatalog
from backend.app.services.graph_store import GraphSnapshot, Neo4jGraphStore
from backend.tests.helpers import upload_and_confirm_property


def _tree_properties(group: dict) -> list[dict]:
    properties = list(group.get("properties", []))
    for child in group.get("groups", []):
        properties.extend(_tree_properties(child))
    return properties


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
        assert {
            item["filename"]
            for item in _tree_properties(listed.json()["property_tree"])
        } == {"mcp.txt"}
        status = client.post(f"{endpoint}/get_processing_status", json={})
        assert status.status_code == 200
        assert "locked" in status.json()
        searched = client.post(f"{endpoint}/search_properties", json={"query": "Neo4j"})
        assert searched.status_code == 200


def test_mcp_list_properties_returns_complete_property_tree():
    with TestClient(app) as client:
        token = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        ).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        project = client.post(
            "/api/projects",
            json={"name": f"MCP Tree-{uuid4().hex}"},
            headers=headers,
        ).json()
        catalog = PropertyCatalog(get_settings())
        common = {
            "project_id": project["id"],
            "property_type": "markdown",
            "status": "active",
            "created_at": "now",
            "updated_at": "now",
        }
        catalog.create(
            project["id"],
            {
                **common,
                "id": "root",
                "filename": "overview.md",
                "relative_path": "properties/overview.md",
                "definition": "An overview of Atlas.",
            },
        )
        catalog.create(
            project["id"],
            {
                **common,
                "id": "manual",
                "filename": "manual.md",
                "relative_path": "properties/Product/Atlas/manual.md",
                "directory": "Product/Atlas",
                "definition": "The Atlas user manual.",
            },
        )
        endpoint = client.post(
            f"/api/projects/{project['id']}/mcp/open", headers=headers
        ).json()["endpoint"]

        response = client.post(f"{endpoint}/list_properties", json={})

        assert response.status_code == 200
        assert response.json() == {
            "property_tree": {
                "group_name": "",
                "group_path": "",
                "properties": [
                    {
                        "property_id": "root",
                        "filename": "overview.md",
                        "property_type": "markdown",
                        "definition": "An overview of Atlas.",
                    }
                ],
                "groups": [
                    {
                        "group_name": "Product",
                        "group_path": "Product",
                        "properties": [],
                        "groups": [
                            {
                                "group_name": "Atlas",
                                "group_path": "Product/Atlas",
                                "properties": [
                                    {
                                        "property_id": "manual",
                                        "filename": "manual.md",
                                        "property_type": "markdown",
                                        "definition": "The Atlas user manual.",
                                    }
                                ],
                                "groups": [],
                            }
                        ],
                    }
                ],
            }
        }


def test_mcp_regroup_property_tree_uses_revision_prompt(monkeypatch):
    import backend.app.api.properties as properties_api

    seen = {}

    class RecordingGAAgent:
        def __init__(self, settings):
            seen["settings"] = settings

        def propose_tree(self, tree_context, revision_prompt):
            from backend.app.services.agents import PropertyTreeProposal

            seen["tree_context"] = tree_context
            seen["revision_prompt"] = revision_prompt
            return PropertyTreeProposal(
                directories={
                    "manual": "Product/Atlas/Guides",
                    "release": "Product/Atlas/Releases",
                },
                filenames={"manual": "manual.md", "release": "release.md"},
            )

    monkeypatch.setattr(properties_api, "GAAgent", RecordingGAAgent)

    with TestClient(app) as client:
        token = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        ).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        project = client.post(
            "/api/projects",
            json={"name": f"MCP Regroup-{uuid4().hex}"},
            headers=headers,
        ).json()
        catalog = PropertyCatalog(get_settings())
        property_root = get_settings().projects_dir / project["id"] / "properties"
        property_root.mkdir(parents=True, exist_ok=True)
        (property_root / "manual.md").write_text("Atlas manual", encoding="utf-8")
        (property_root / "release.md").write_text("Atlas release", encoding="utf-8")
        common = {
            "project_id": project["id"],
            "property_type": "markdown",
            "status": "active",
            "created_at": "now",
            "updated_at": "now",
        }
        catalog.create(
            project["id"],
            {
                **common,
                "id": "manual",
                "filename": "manual.md",
                "relative_path": "properties/manual.md",
                "definition": "The Atlas user manual.",
            },
        )
        catalog.create(
            project["id"],
            {
                **common,
                "id": "release",
                "filename": "release.md",
                "relative_path": "properties/release.md",
                "definition": "The Atlas release checklist.",
            },
        )
        endpoint = client.post(
            f"/api/projects/{project['id']}/mcp/open", headers=headers
        ).json()["endpoint"]

        response = client.post(
            f"{endpoint}/regroup_properties",
            json={
                "revision_prompt": "Separate Atlas guides from release documents."
            },
        )

        assert response.status_code == 200
        assert seen["revision_prompt"] == (
            "Separate Atlas guides from release documents."
        )
        assert seen["tree_context"]["properties"] == [
            {
                "property_id": "manual",
                "filename": "manual.md",
                "property_type": "markdown",
                "definition": "The Atlas user manual.",
            },
            {
                "property_id": "release",
                "filename": "release.md",
                "property_type": "markdown",
                "definition": "The Atlas release checklist.",
            },
        ]
        rows = {item["id"]: item for item in response.json()["properties"]}
        assert rows["manual"]["relative_path"] == (
            "properties/Product/Atlas/Guides/manual.md"
        )
        assert rows["release"]["relative_path"] == (
            "properties/Product/Atlas/Releases/release.md"
        )
        assert response.json()["job_id"]
        assert (
            get_settings().projects_dir
            / project["id"]
            / rows["manual"]["relative_path"]
        ).is_file()


def test_mcp_list_entities_can_filter_by_property_id():
    with TestClient(app) as client:
        token = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        ).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        project = client.post(
            "/api/projects",
            json={"name": f"MCP Entity Filter-{uuid4().hex}"},
            headers=headers,
        ).json()
        graph_store = Neo4jGraphStore(get_settings())
        graph_store.write_snapshot(
            GraphSnapshot(
                project_id=project["id"],
                snapshot_id="entity-filter",
                properties=[],
                entities=[
                    {
                        "id": "atlas",
                        "name": "Atlas",
                        "definition": "A document product.",
                        "source_property_ids": ["manual", "architecture"],
                    },
                    {
                        "id": "python",
                        "name": "Python",
                        "definition": "A programming language.",
                        "source_property_ids": ["manual"],
                    },
                    {
                        "id": "release",
                        "name": "Atlas 2.0",
                        "definition": "A product release.",
                        "source_property_ids": ["release-notes"],
                    },
                    {
                        "id": "legacy",
                        "name": "Legacy Guide",
                        "definition": "An older guide.",
                        "source_contexts": [
                            {
                                "property_id": "manual",
                                "text": "The legacy guide supplements the manual.",
                            }
                        ],
                    },
                ],
                property_edges=[],
                entity_edges=[],
            )
        )
        graph_store.activate(project["id"], "entity-filter")
        graph_store.close()
        endpoint = client.post(
            f"/api/projects/{project['id']}/mcp/open", headers=headers
        ).json()["endpoint"]

        all_entities = client.post(f"{endpoint}/list_entities", json={})
        manual_entities = client.post(
            f"{endpoint}/list_entities", json={"property_id": "manual"}
        )

        assert all_entities.status_code == 200
        assert {item["id"] for item in all_entities.json()["entities"]} == {
            "atlas",
            "python",
            "release",
            "legacy",
        }
        assert manual_entities.status_code == 200
        assert {item["id"] for item in manual_entities.json()["entities"]} == {
            "atlas",
            "python",
            "legacy",
        }


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
        assert client.post(f"{endpoint}/ask_ai_query", json={"query": "What is here?"}).status_code == 404
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
