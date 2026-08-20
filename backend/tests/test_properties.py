import io
import json
import re
import time
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.db import connect, initialize
from backend.app.main import app
from backend.app.services.agents import DefinitionResult, PropertyTreeProposal
from backend.app.services.catalog import PropertyCatalog
from backend.app.services.storage import write_property_text
from backend.tests.helpers import upload_and_confirm_property


def _auth(client):
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_staged_import_sends_direct_bounded_text_to_definition_agent_and_keeps_full_text(
    tmp_path, monkeypatch
):
    import backend.app.api.properties as properties_api

    settings = properties_api.Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    full_text = (
        "# Architecture\nAtlas uses Neo4j for relation-aware retrieval.\n\n"
        + ("Detailed architecture notes.\n" * 2_000)
    )
    recorded = {}

    def generate_metadata(
        _settings,
        _filename,
        _kind,
        _path,
        _comment,
        *,
        full_text,
        extraction_text,
        existing_entities,
    ):
        recorded.update(
            full_text=full_text,
            extraction_text=extraction_text,
            existing_entities=existing_entities,
        )
        return DefinitionResult(
            "Atlas architecture and Meridian migration notes.",
            "",
            full_text,
        )

    monkeypatch.setattr(
        properties_api, "generate_property_metadata", generate_metadata
    )

    result = properties_api._stage_property_import(
        settings,
        "project",
        "atlas.md",
        full_text.encode("utf-8"),
        "text/markdown",
        "Focus on product relationships.",
    )
    staged = properties_api.PropertyImportStore(settings).get(
        "project", result["import_id"]
    )

    assert recorded["full_text"] == full_text.strip()
    assert recorded["extraction_text"] == full_text.strip()[:24_000].rstrip()
    assert recorded["existing_entities"] == []
    assert staged is not None
    assert staged["content"] == full_text.strip()
    assert staged["extraction"]["original_character_count"] == len(full_text.strip())
    assert staged["extraction"]["selected_character_count"] == 24_000
    assert len(staged["extraction"]["chunks"]) == 1


def test_direct_property_upload_uses_definition_agent_property_identifier(
    tmp_path, monkeypatch
):
    import backend.app.api.properties as properties_api

    settings = properties_api.Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES (?,?,?,?)",
            ("project", "Project", "now", "now"),
        )
    monkeypatch.setattr(
        properties_api,
        "generate_property_metadata",
        lambda *_args, **_kwargs: DefinitionResult(
            "An Atlas operations guide.",
            "",
            "Atlas operations content.",
            "atlas-operations-guide",
        ),
    )
    scheduled = []
    monkeypatch.setattr(
        properties_api,
        "_schedule",
        lambda _background_tasks, function, *args: scheduled.append(
            (function, args)
        ),
    )

    result = properties_api._enqueue_property(
        settings,
        "project",
        "notes.txt",
        b"Atlas operations content.",
        "text/plain",
    )

    assert result["property_id"] == "atlas-operations-guide"
    assert PropertyCatalog(settings).get("project", "atlas-operations-guide")
    assert len(scheduled) == 1


def test_image_property_is_accepted_and_processed_without_entity_input(monkeypatch):
    import backend.app.api.properties as properties_api

    monkeypatch.setattr(
        properties_api,
        "generate_property_metadata",
        lambda *_args, **_kwargs: DefinitionResult(
            "An architecture diagram for Atlas.",
            "atlas-architecture.png",
            "An architecture diagram shows the Atlas components.",
        ),
    )
    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post("/api/projects", json={"name": f"Images-{uuid4().hex}"}, headers=headers).json()
        upload_and_confirm_property(client, project["id"], headers, "diagram.png", b"fake image", "image/png")
        prop = client.get(f"/api/projects/{project['id']}/properties", headers=headers).json()[0]
        assert prop["property_type"] == "image"


def test_property_content_endpoint_returns_persisted_pure_text():
    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post(
            "/api/projects",
            json={"name": f"Pure Content-{uuid4().hex}"},
            headers=headers,
        ).json()
        property_id = str(uuid4())
        project_root = get_settings().projects_dir / project["id"]
        original = project_root / "properties" / "revenue.xlsx"
        original.write_bytes(b"binary workbook")
        PropertyCatalog(get_settings()).create(
            project["id"],
            {
                "id": property_id,
                "project_id": project["id"],
                "filename": "revenue.xlsx",
                "property_type": "spreadsheet",
                "relative_path": "properties/revenue.xlsx",
                "definition": "The revenue workbook.",
                "status": "active",
                "created_at": "now",
                "updated_at": "now",
            },
        )
        write_property_text(
            get_settings(),
            project["id"],
            property_id,
            "Product | Revenue\nAtlas | 125",
        )

        response = client.get(
            f"/api/projects/{project['id']}/properties/{property_id}/content",
            headers=headers,
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert response.text == "Product | Revenue\nAtlas | 125"


def test_upload_returns_filename_suggestion_without_storing_it_as_property_metadata(monkeypatch):
    import backend.app.api.properties as properties_api

    monkeypatch.setattr(
        properties_api,
        "generate_property_metadata",
        lambda *_args, **_kwargs: DefinitionResult(
            "A release plan for Atlas.",
            "atlas-release-plan.md",
            property_id="atlas-planning-document",
        ),
        raising=False,
    )
    class RecordingGAAgent:
        def __init__(self, settings):
            self.settings = settings

        def plan_import(self, tree_context, items, import_context):
            assert tree_context == {}
            assert items[0]["definition"] == "A release plan for Atlas."
            property_id = items[0]["property_id"]
            return PropertyTreeProposal(
                {property_id: "Products/Atlas"},
                {property_id: "atlas-release-plan.md"},
            )

    monkeypatch.setattr(
        properties_api, "GAAgent", RecordingGAAgent
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
        assert property_id == "atlas-planning-document"
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


def test_batch_stage_generates_metadata_for_every_file_before_confirmation(monkeypatch):
    import backend.app.api.properties as properties_api

    analyzed = []

    def generate_metadata(_settings, filename, _kind, _path, _comment):
        analyzed.append(filename)
        stem = filename.rsplit(".", 1)[0]
        return DefinitionResult(
            f"A concise guide to {stem}.",
            f"{stem}-guide.md",
        )

    monkeypatch.setattr(
        properties_api,
        "generate_property_metadata",
        generate_metadata,
    )
    filename_calls = []

    class RecordingGAAgent:
        def __init__(self, settings):
            self.settings = settings

        def plan_import(self, tree_context, items, import_context):
            filename_calls.append((tree_context, items, import_context))
            return PropertyTreeProposal(
                {item["property_id"]: "Product Guides" for item in items},
                {
                    item["property_id"]: f"{item['original_filename'].rsplit('.', 1)[0]}-guide.md"
                    for item in items
                },
            )

    monkeypatch.setattr(
        properties_api, "GAAgent", RecordingGAAgent
    )

    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post(
            "/api/projects",
            json={"name": f"Batch Stage-{uuid4().hex}"},
            headers=headers,
        ).json()

        staged = client.post(
            f"/api/projects/{project['id']}/property-import-batches",
            files=[
                ("files", ("atlas.md", io.BytesIO(b"Atlas"), "text/markdown")),
                ("files", ("nova.md", io.BytesIO(b"Nova"), "text/markdown")),
            ],
            data={"comment": "Group these product guides together."},
            headers=headers,
        )

        assert staged.status_code == 202
        payload = staged.json()
        assert payload["status"] == "awaiting_confirmation"
        assert sorted(analyzed) == ["atlas.md", "nova.md"]
        assert len(filename_calls) == 1
        assert filename_calls[0][0] == {}
        assert filename_calls[0][2] == "Group these product guides together."
        assert [item["definition"] for item in filename_calls[0][1]] == [
            "A concise guide to atlas.",
            "A concise guide to nova.",
        ]
        assert [item["original_filename"] for item in payload["items"]] == [
            "atlas.md",
            "nova.md",
        ]
        assert [item["suggested_filename"] for item in payload["items"]] == [
            "atlas-guide.md",
            "nova-guide.md",
        ]
        assert [item["suggested_directory"] for item in payload["items"]] == [
            "Product Guides",
            "Product Guides",
        ]
        assert [item["definition"] for item in payload["items"]] == [
            "A concise guide to atlas.",
            "A concise guide to nova.",
        ]
        imports_root = get_settings().projects_dir / project["id"] / "imports"
        for item in payload["items"]:
            staged_record = (
                imports_root / item["import_id"] / "import.json"
            ).read_text(encoding="utf-8")
            assert "suggested_filename" not in staged_record
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


def test_batch_stage_stream_reports_each_file_before_metadata_generation(monkeypatch):
    import backend.app.api.properties as properties_api

    analyzed = []

    def generate_metadata(_settings, filename, _kind, _path, _comment):
        analyzed.append(filename)
        return DefinitionResult(
            f"A concise guide to {filename}.",
            f"{filename.rsplit('.', 1)[0]}-guide.md",
        )

    monkeypatch.setattr(
        properties_api,
        "generate_property_metadata",
        generate_metadata,
    )
    filename_calls = []

    class RecordingGAAgent:
        def __init__(self, settings):
            self.settings = settings

        def plan_import(self, tree_context, items, import_context):
            filename_calls.append(items)
            return PropertyTreeProposal(
                {item["property_id"]: "Product Guides" for item in items},
                {
                    item["property_id"]: f"{item['original_filename'].rsplit('.', 1)[0]}-guide.md"
                    for item in items
                },
            )

    monkeypatch.setattr(
        properties_api, "GAAgent", RecordingGAAgent
    )

    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post(
            "/api/projects",
            json={"name": f"Streamed Batch-{uuid4().hex}"},
            headers=headers,
        ).json()

        response = client.post(
            f"/api/projects/{project['id']}/property-import-batches/stream",
            files=[
                ("files", ("atlas.md", io.BytesIO(b"Atlas"), "text/markdown")),
                ("files", ("nova.md", io.BytesIO(b"Nova"), "text/markdown")),
            ],
            headers=headers,
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines() if line]
    event_types = [event["type"] for event in events]
    assert event_types[0] == "batch_started"
    assert event_types.count("file_started") == 2
    assert event_types.count("file_analyzed") == 2
    assert event_types[-2:] == [
        "import_plan_generation_started",
        "batch_completed",
    ]
    assert sorted(analyzed) == ["atlas.md", "nova.md"]
    assert len(filename_calls) == 1
    assert [
        (event["index"], event["total"], event["filename"])
        for event in events
        if event["type"] == "file_started"
    ] == [(1, 2, "atlas.md"), (2, 2, "nova.md")]
    completed = events[-1]
    assert completed["status"] == "awaiting_confirmation"
    assert [item["original_filename"] for item in completed["items"]] == [
        "atlas.md",
        "nova.md",
    ]


def test_batch_stage_stream_sends_keepalive_while_metadata_generation_is_slow(
    monkeypatch,
):
    import backend.app.api.properties as properties_api

    monkeypatch.setattr(properties_api, "PROPERTY_IMPORT_KEEPALIVE_SECONDS", 0.01)

    def generate_metadata(_settings, filename, _kind, _path, _comment):
        time.sleep(0.04)
        return DefinitionResult(
            f"A concise guide to {filename}.",
            filename,
        )

    monkeypatch.setattr(
        properties_api,
        "generate_property_metadata",
        generate_metadata,
    )
    class LocalGAAgent:
        def __init__(self, settings):
            self.settings = settings

        def plan_import(self, _tree_context, items, _import_context):
            return PropertyTreeProposal(
                {item["property_id"]: "" for item in items},
                {
                    item["property_id"]: item["original_filename"]
                    for item in items
                },
            )

    monkeypatch.setattr(
        properties_api,
        "GAAgent",
        LocalGAAgent,
    )

    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post(
            "/api/projects",
            json={"name": f"Stream Keepalive-{uuid4().hex}"},
            headers=headers,
        ).json()
        response = client.post(
            f"/api/projects/{project['id']}/property-import-batches/stream",
            files=[
                ("files", ("atlas.md", io.BytesIO(b"Atlas"), "text/markdown")),
            ],
            headers=headers,
        )

    events = [json.loads(line) for line in response.text.splitlines() if line]
    keepalives = [event for event in events if event["type"] == "keepalive"]
    assert response.status_code == 200
    assert keepalives
    assert keepalives[0] == {
        "type": "keepalive",
        "batch_id": events[0]["batch_id"],
        "index": 1,
        "total": 1,
        "filename": "atlas.md",
    }
    assert events[-1]["type"] == "batch_completed"


def test_batch_stage_stream_preserves_chinese_filenames(monkeypatch):
    import backend.app.api.properties as properties_api

    monkeypatch.setattr(
        properties_api,
        "generate_property_metadata",
        lambda *_args: DefinitionResult(
            "A product manual covering installation and usage.",
            "产品手册.md",
        ),
    )
    class RecordingGAAgent:
        def __init__(self, settings):
            self.settings = settings

        def plan_import(self, tree_context, items, import_context):
            property_id = items[0]["property_id"]
            return PropertyTreeProposal(
                {property_id: "产品/手册"},
                {property_id: "产品手册.md"},
            )

    monkeypatch.setattr(
        properties_api, "GAAgent", RecordingGAAgent
    )

    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post(
            "/api/projects",
            json={"name": f"Chinese Filename-{uuid4().hex}"},
            headers=headers,
        ).json()
        response = client.post(
            f"/api/projects/{project['id']}/property-import-batches/stream",
            files=[
                (
                    "files",
                    ("产品使用说明.md", io.BytesIO(b"Product manual"), "text/markdown"),
                )
            ],
            headers=headers,
        )

    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert response.status_code == 200
    assert next(
        event["filename"] for event in events if event["type"] == "file_started"
    ) == "产品使用说明.md"
    completed = events[-1]
    assert completed["type"] == "batch_completed"
    assert completed["items"][0]["original_filename"] == "产品使用说明.md"
    assert completed["items"][0]["suggested_filename"] == "产品手册.md"


def test_batch_confirm_creates_one_job_and_schedules_every_property(monkeypatch):
    import backend.app.api.properties as properties_api

    scheduled = []
    monkeypatch.setattr(
        properties_api,
        "generate_property_metadata",
        lambda _settings, filename, _kind, _path, _comment: DefinitionResult(
            f"Definition for {filename}.",
            filename,
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
            json={"name": f"Batch Confirm-{uuid4().hex}"},
            headers=headers,
        ).json()
        staged = client.post(
            f"/api/projects/{project['id']}/property-import-batches",
            files=[
                ("files", ("first.md", io.BytesIO(b"First"), "text/markdown")),
                ("files", ("second.md", io.BytesIO(b"Second"), "text/markdown")),
            ],
            data={"comment": "Product documentation"},
            headers=headers,
        ).json()

        confirmed = client.post(
            f"/api/projects/{project['id']}/property-import-batches/{staged['batch_id']}/confirm",
            json={
                "items": [
                    {
                        "import_id": staged["items"][0]["import_id"],
                        "filename": "first-guide.md",
                    },
                    {
                        "import_id": staged["items"][1]["import_id"],
                        "filename": "second-guide.md",
                    },
                ]
            },
            headers=headers,
        )

        assert confirmed.status_code == 202
        result = confirmed.json()
        assert result["status"] == "queued"
        assert all(
            re.fullmatch(r"[A-Za-z0-9_-]+", item["property_id"])
            for item in result["properties"]
        )
        assert [item["filename"] for item in result["properties"]] == [
            "first-guide.md",
            "second-guide.md",
        ]
        assert {item["job_id"] for item in result["properties"]} == {
            result["job_id"]
        }
        assert len(scheduled) == 1
        function, args = scheduled[0]
        assert function.__name__ == "run_batch_pipeline"
        assert args[1:3] == (project["id"], result["job_id"])
        assert [item["filename"] for item in args[3]] == [
            "first-guide.md",
            "second-guide.md",
        ]
        catalog_rows = PropertyCatalog(get_settings()).list(project["id"])
        assert [row["definition"] for row in catalog_rows] == [
            "Definition for first.md.",
            "Definition for second.md.",
        ]
        with connect(get_settings().sqlite_path) as db:
            assert db.execute(
                "SELECT COUNT(*) FROM jobs WHERE project_id=?",
                (project["id"],),
            ).fetchone()[0] == 1
        imports_root = get_settings().projects_dir / project["id"] / "imports"
        assert not (imports_root / "batches" / f"{staged['batch_id']}.json").exists()
        assert all(
            not (imports_root / item["import_id"]).exists()
            for item in staged["items"]
        )


def test_batch_cancel_removes_all_staged_files(monkeypatch):
    import backend.app.api.properties as properties_api

    monkeypatch.setattr(
        properties_api,
        "generate_property_metadata",
        lambda _settings, filename, _kind, _path, _comment: DefinitionResult(
            f"Definition for {filename}.",
            filename,
        ),
    )

    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post(
            "/api/projects",
            json={"name": f"Batch Cancel-{uuid4().hex}"},
            headers=headers,
        ).json()
        staged = client.post(
            f"/api/projects/{project['id']}/property-import-batches",
            files=[
                ("files", ("first.md", io.BytesIO(b"First"), "text/markdown")),
                ("files", ("second.md", io.BytesIO(b"Second"), "text/markdown")),
            ],
            headers=headers,
        ).json()
        imports_root = get_settings().projects_dir / project["id"] / "imports"

        cancelled = client.delete(
            f"/api/projects/{project['id']}/property-import-batches/{staged['batch_id']}",
            headers=headers,
        )

        assert cancelled.status_code == 204
        assert not (imports_root / "batches" / f"{staged['batch_id']}.json").exists()
        assert all(
            not (imports_root / item["import_id"]).exists()
            for item in staged["items"]
        )
        assert client.get(
            f"/api/projects/{project['id']}/properties",
            headers=headers,
        ).json() == []


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

        def propose_tree(self, tree_context, revision_prompt):
            from backend.app.services.agents import PropertyTreeProposal

            seen["tree_context"] = tree_context
            seen["revision_prompt"] = revision_prompt
            return PropertyTreeProposal(
                directories={
                    "manual": "Product/Atlas/Guides",
                    "release": "Product/Atlas/Releases",
                },
                filenames={
                    "manual": "atlas-guide.md",
                    "release": "atlas-release.md",
                },
            )

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
        assert (property_root / "atlas-manual.md").is_file()
        assert not (property_root / "Product").exists()
        proposal = regrouped.json()
        assert proposal["changes"] == [
            {
                "property_id": "manual",
                "current_directory": "",
                "proposed_directory": "Product/Atlas/Guides",
                "current_filename": "atlas-manual.md",
                "proposed_filename": "atlas-guide.md",
                "definition": "A user manual for Atlas.",
                "changed": True,
            },
            {
                "property_id": "release",
                "current_directory": "",
                "proposed_directory": "Product/Atlas/Releases",
                "current_filename": "atlas-release.md",
                "proposed_filename": "atlas-release.md",
                "definition": "The Atlas release checklist.",
                "changed": True,
            },
        ]

        confirmed = client.post(
            f"/api/projects/{project['id']}/properties/regroup/confirm",
            json={
                "catalog_signature": proposal["catalog_signature"],
                "items": [
                    {
                        "property_id": "manual",
                        "directory": "Product/Atlas/Guides",
                        "filename": "atlas-user-guide.md",
                    },
                    {
                        "property_id": "release",
                        "directory": "Product/Atlas/Releases",
                        "filename": "atlas-release.md",
                    },
                ],
            },
            headers=headers,
        )

        assert confirmed.status_code == 200
        rows = {row["id"]: row for row in confirmed.json()["properties"]}
        assert rows["manual"]["relative_path"] == (
            "properties/Product/Atlas/Guides/atlas-user-guide.md"
        )
        assert rows["release"]["relative_path"] == (
            "properties/Product/Atlas/Releases/atlas-release.md"
        )
        assert (
            get_settings().projects_dir
            / project["id"]
            / rows["manual"]["relative_path"]
        ).is_file()


def test_regroup_confirmation_rejects_a_stale_catalog_signature(monkeypatch):
    import backend.app.api.properties as properties_api
    from backend.app.services.agents import PropertyTreeProposal

    class RecordingGAAgent:
        def __init__(self, settings):
            self.settings = settings

        def propose_tree(self, tree_context, revision_prompt):
            return PropertyTreeProposal(
                directories={"manual": "Product"},
                filenames={"manual": "manual.md"},
            )

    monkeypatch.setattr(properties_api, "GAAgent", RecordingGAAgent)

    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post(
            "/api/projects",
            json={"name": f"Stale Regroup-{uuid4().hex}"},
            headers=headers,
        ).json()
        root = get_settings().projects_dir / project["id"] / "properties"
        (root / "manual.md").write_text("Manual", encoding="utf-8")
        catalog = PropertyCatalog(get_settings())
        catalog.create(project["id"], {
            "id": "manual", "project_id": project["id"], "filename": "manual.md",
            "property_type": "markdown", "relative_path": "properties/manual.md",
            "definition": "A manual.", "status": "active", "created_at": "now", "updated_at": "now",
        })
        proposal = client.post(
            f"/api/projects/{project['id']}/properties/regroup",
            json={"revision_prompt": "Move the manual into Product."},
            headers=headers,
        ).json()
        catalog.update(project["id"], "manual", {"updated_at": "later"})

        response = client.post(
            f"/api/projects/{project['id']}/properties/regroup/confirm",
            json={
                "catalog_signature": proposal["catalog_signature"],
                "items": [{"property_id": "manual", "directory": "Product", "filename": "manual.md"}],
            },
            headers=headers,
        )

        assert response.status_code == 409
        assert (root / "manual.md").is_file()


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


def test_batch_remove_properties_uses_one_graph_pruning_job():
    with TestClient(app) as client:
        headers = _auth(client)
        project = client.post(
            "/api/projects",
            json={"name": f"Batch Remove-{uuid4().hex}"},
            headers=headers,
        ).json()
        first = upload_and_confirm_property(
            client,
            project["id"],
            headers,
            "first.txt",
            b"First property",
            "text/plain",
        )
        second = upload_and_confirm_property(
            client,
            project["id"],
            headers,
            "second.txt",
            b"Second property",
            "text/plain",
        )

        removed = client.post(
            f"/api/projects/{project['id']}/properties/batch-delete",
            json={"property_ids": [first["property_id"], second["property_id"]]},
            headers=headers,
        )

        assert removed.status_code == 202
        assert removed.json()["property_ids"] == [
            first["property_id"],
            second["property_id"],
        ]
        assert client.get(
            f"/api/projects/{project['id']}/properties",
            headers=headers,
        ).json() == []


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
        assert set(routes) == {"dg_agent_route", "ga_agent_route", "entity_agent_route", "ai_query_route", "shared_embedding_route"}
        assert all("model" in route and "provider_type" in route for route in routes.values())
