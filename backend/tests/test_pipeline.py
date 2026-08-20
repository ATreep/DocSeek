from pathlib import Path
from threading import Barrier, Event, Lock
import time

from backend.app.config import Settings
from backend.app.db import connect, initialize
import json

from backend.app.services.catalog import PropertyCatalog
from backend.app.services.graph_store import GraphSnapshot, LocalGraphStore, extract_entities
from backend.app.services.pipeline import (
    _job_heartbeat,
    _merge_entity_delta,
    _prepare_entity_document,
    _should_extract_entities_incrementally,
    _transition_job,
    run_pipeline,
    run_property_removal,
)
from backend.app.services.storage import safe_directory


def test_prepare_entity_document_selects_bounded_text_and_keeps_full_source():
    full_text = (
        "PORTAL HEADER\nPage 1 of 2\n\n"
        "# Architecture\nAtlas uses Neo4j for relation-aware retrieval.\n\n"
        "# Migration\nLegacy Meridian depends on Atlas during migration.\n\n"
        "PORTAL HEADER\nPage 2 of 2\n"
    )
    document = {
        "project_id": "project",
        "property_id": "property",
        "property_type": "text",
        "filename": "atlas.md",
        "definition": "Atlas architecture and migration notes.",
        "import_context": "Focus on product relationships.",
        "text": full_text,
    }

    prepared, selection = _prepare_entity_document(
        document,
        [
            {
                "id": "meridian",
                "name": "Meridian Platform",
                "aliases": ["Legacy Meridian"],
                "definition": "A migration platform.",
            }
        ],
        max_chars=180,
    )

    assert prepared["original_text"] == full_text
    assert prepared["text"] == selection.text
    assert "Atlas uses Neo4j" in prepared["text"]
    assert "Legacy Meridian" in prepared["text"]
    assert "Page 1 of 2" not in prepared["text"]
    assert prepared["extraction_chunks"][0]["start"] >= 0


def test_entity_extraction_excludes_image_documents_by_contract(tmp_path):
    entities, _ = extract_entities([{"project_id": "p", "property_id": "text-1", "text": "Neo4j connects DocSeek."}])
    assert {item["name"] for item in entities} == {"Neo4j", "DocSeek"}
    entities_without_image, _ = extract_entities([])
    assert entities_without_image == []


def test_local_graph_snapshot_is_not_sqlite_canonical_data(tmp_path):
    settings = type("Settings", (), {"data_dir": tmp_path})()
    store = LocalGraphStore(settings)
    store.write_snapshot(GraphSnapshot("p", "s1", [{"id": "prop-1", "definition": "image"}], [], [], []))
    store.activate("p", "s1")
    assert store.read("p").properties[0]["id"] == "prop-1"
    assert not list(tmp_path.glob("*.sqlite3"))


def test_candidate_snapshot_does_not_replace_active_until_activation(tmp_path):
    settings = type("Settings", (), {"data_dir": tmp_path})()
    store = LocalGraphStore(settings)
    store.write_snapshot(GraphSnapshot("p", "active", [{"id": "old"}], [], [], []))
    store.activate("p", "active")
    store.write_snapshot(GraphSnapshot("p", "candidate", [{"id": "new"}], [], [], []))
    assert store.read("p").properties[0]["id"] == "old"
    store.activate("p", "candidate")
    assert store.read("p").properties[0]["id"] == "new"


def test_first_candidate_snapshot_is_not_readable_until_activation(tmp_path):
    settings = type("Settings", (), {"data_dir": tmp_path})()
    store = LocalGraphStore(settings)
    store.write_snapshot(GraphSnapshot("p", "candidate", [{"id": "new"}], [], [], []))
    assert store.read("p") is None
    store.activate("p", "candidate")
    assert store.read("p").properties[0]["id"] == "new"


def test_property_catalog_removes_legacy_filename_suggestion_metadata(tmp_path):
    settings = Settings(data_dir=tmp_path)
    catalog = PropertyCatalog(settings)
    path = settings.projects_dir / "p" / "jobs" / "property-catalog.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            [
                {
                    "id": "property-1",
                    "filename": "notes.md",
                    "filename_suggestion": "suggested-notes.md",
                }
            ]
        ),
        encoding="utf-8",
    )

    records = catalog.list("p")

    assert "filename_suggestion" not in records[0]
    assert "filename_suggestion" not in path.read_text(encoding="utf-8")


def test_group_directories_accept_semantic_names_with_spaces():
    assert safe_directory("Corporate Administration/HR") == Path(
        "Corporate Administration/HR"
    )


def test_group_directories_accept_chinese_names():
    assert safe_directory("人力资源/人员简历") == Path("人力资源/人员简历")


def test_incremental_entity_extraction_is_only_used_for_new_properties():
    active_properties = {"existing": {"id": "existing"}}

    assert _should_extract_entities_incrementally("add", "new", active_properties)
    assert _should_extract_entities_incrementally("retry", "new", active_properties)
    assert not _should_extract_entities_incrementally(
        "add", "existing", active_properties
    )
    assert not _should_extract_entities_incrementally(
        "replace", "new", active_properties
    )
    assert not _should_extract_entities_incrementally(
        "remove", "new", active_properties
    )


def test_entity_delta_merges_provenance_and_replaces_directed_relation_type():
    nodes, edges = _merge_entity_delta(
        [
            {
                "id": "atlas",
                "name": "Atlas",
                "definition": "A release product.",
                "source_property_ids": ["alpha"],
                "source_contexts": [
                    {"property_id": "alpha", "text": "Atlas uses Neo4j."}
                ],
            },
            {"id": "neo4j", "name": "Neo4j", "definition": "A graph database."},
        ],
        [{"source": "atlas", "target": "neo4j", "type": "USES"}],
        [
            {
                "id": "atlas",
                "name": "Atlas",
                "definition": "A release management product.",
                "source_property_ids": ["beta"],
                "source_contexts": [
                    {"property_id": "beta", "text": "Atlas stores metadata in Neo4j."}
                ],
            }
        ],
        [{"source": "atlas", "target": "neo4j", "type": "STORES_METADATA_IN"}],
    )

    atlas = next(node for node in nodes if node["id"] == "atlas")
    assert atlas["definition"] == "A release management product."
    assert atlas["source_property_ids"] == ["alpha", "beta"]
    assert [context["property_id"] for context in atlas["source_contexts"]] == [
        "alpha",
        "beta",
    ]
    assert edges == [
        {
            "source": "atlas",
            "target": "neo4j",
            "type": "STORES_METADATA_IN",
        }
    ]


def test_ga_directory_suggestion_is_applied_with_candidate_activation(
    tmp_path, monkeypatch
):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    project_id, property_id, job_id = "ga-project", "ga-property", "ga-job"
    source = settings.projects_dir / project_id / "properties" / "guide.md"
    source.parent.mkdir(parents=True)
    source.write_text("Guidance for deployment runbooks.", encoding="utf-8")
    with connect(settings.sqlite_path) as db:
        db.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES (?,?,?,?)", (project_id, "GA", "now", "now"))
        db.execute("INSERT INTO jobs(id,project_id,stage,status,heartbeat) VALUES (?,?,?,?,?)", (job_id, project_id, "queued", "queued", "now"))
    PropertyCatalog(settings).create(project_id, {"id": property_id, "project_id": project_id, "filename": "guide.md", "property_type": "markdown", "relative_path": "properties/guide.md", "definition": None, "filename_suggestion": None, "status": "queued", "created_at": "now", "updated_at": "now"})
    run_pipeline(settings, project_id, property_id, job_id, "guide.md", "markdown", source)

    row = PropertyCatalog(settings).get(project_id, property_id)
    assert row["relative_path"] == "properties/Guide/guide.md"
    assert (settings.projects_dir / project_id / row["relative_path"]).read_text(encoding="utf-8") == "Guidance for deployment runbooks."
    assert not source.exists()


def test_pipeline_passes_property_metadata_and_current_tree_to_ga_agent(
    tmp_path, monkeypatch
):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    project_id, property_id, job_id = "semantic-ga", "product-a", "semantic-job"
    property_root = settings.projects_dir / project_id / "properties"
    source = property_root / "product-A-manual.md"
    existing = property_root / "Product" / "Product B" / "product-B-usage.md"
    source.parent.mkdir(parents=True)
    existing.parent.mkdir(parents=True)
    source.write_text("Product A manual.", encoding="utf-8")
    existing.write_text("Product B usage.", encoding="utf-8")
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES (?,?,?,?)",
            (project_id, "Semantic GA", "now", "now"),
        )
        db.execute(
            "INSERT INTO jobs(id,project_id,stage,status,heartbeat) VALUES (?,?,?,?,?)",
            (job_id, project_id, "queued", "queued", "now"),
        )
    catalog = PropertyCatalog(settings)
    catalog.create(
        project_id,
        {
            "id": "product-b",
            "project_id": project_id,
            "filename": "product-B-usage.md",
            "property_type": "markdown",
            "relative_path": "properties/Product/Product B/product-B-usage.md",
            "definition": "A usage guide for product B.",
            "status": "active",
            "created_at": "now",
            "updated_at": "now",
        },
    )
    catalog.create(
        project_id,
        {
            "id": property_id,
            "project_id": project_id,
            "filename": "product-A-manual.md",
            "property_type": "markdown",
            "relative_path": "properties/product-A-manual.md",
            "definition": None,
            "status": "queued",
            "created_at": "now",
            "updated_at": "now",
        },
    )
    seen = {}

    class RecordingGAAgent:
        def __init__(self, settings):
            seen["settings"] = settings

        def suggest_path(
            self,
            definition,
            tree_context,
            *,
            filename,
            property_type,
            user_context,
        ):
            seen.update(
                definition=definition,
                tree_context=tree_context,
                filename=filename,
                property_type=property_type,
                user_context=user_context,
            )
            return "Product/Product A"

    monkeypatch.setattr("backend.app.services.pipeline.GAAgent", RecordingGAAgent)

    run_pipeline(
        settings,
        project_id,
        property_id,
        job_id,
        "product-A-manual.md",
        "markdown",
        source,
        comment="Keep this with the Product A architecture files.",
        definition_override="A user manual for product A.",
    )

    assert seen["settings"] is settings
    assert seen["definition"] == "A user manual for product A."
    assert seen["filename"] == "product-A-manual.md"
    assert seen["property_type"] == "markdown"
    assert seen["user_context"] == (
        "Keep this with the Product A architecture files."
    )
    assert seen["tree_context"] == {
        "group_name": "",
        "group_path": "",
        "properties": [],
        "groups": [
            {
                "group_name": "Product",
                "group_path": "Product",
                "properties": [],
                "groups": [
                    {
                        "group_name": "Product B",
                        "group_path": "Product/Product B",
                        "properties": [
                            {
                                "property_id": "product-b",
                                "filename": "product-B-usage.md",
                                "property_type": "markdown",
                                "definition": "A usage guide for product B.",
                            }
                        ],
                        "groups": [],
                    }
                ],
            }
        ],
    }
    row = catalog.get(project_id, property_id)
    assert row["relative_path"] == (
        "properties/Product/Product A/product-A-manual.md"
    )


def test_add_extracts_only_new_property_and_remove_prunes_graphs_without_models(
    tmp_path, monkeypatch
):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    project_id = "full-rebuild"
    property_root = settings.projects_dir / project_id / "properties"
    first_path = property_root / "alpha.txt"
    second_path = property_root / "beta.txt"
    first_path.parent.mkdir(parents=True)
    first_path.write_text("Alpha uses CoreDB.", encoding="utf-8")
    second_path.write_text("Beta extends Alpha.", encoding="utf-8")
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES (?,?,?,?)",
            (project_id, "Full rebuild", "now", "now"),
        )
        for job_id in ("add-job", "remove-job"):
            db.execute(
                "INSERT INTO jobs(id,project_id,stage,status,heartbeat) VALUES (?,?,?,?,?)",
                (job_id, project_id, "queued", "queued", "now"),
            )
    catalog = PropertyCatalog(settings)
    catalog.create(
        project_id,
        {
            "id": "alpha",
            "project_id": project_id,
            "filename": "alpha.txt",
            "property_type": "text",
            "relative_path": "properties/alpha.txt",
            "definition": "The Alpha design.",
            "status": "active",
            "created_at": "now",
            "updated_at": "now",
        },
    )
    catalog.create(
        project_id,
        {
            "id": "beta",
            "project_id": project_id,
            "filename": "beta.txt",
            "property_type": "text",
            "relative_path": "properties/beta.txt",
            "definition": None,
            "status": "queued",
            "created_at": "now",
            "updated_at": "now",
        },
    )
    active_store = LocalGraphStore(settings)
    active_store.write_snapshot(
        GraphSnapshot(
            project_id,
            "active-before-add",
            [
                {
                    "id": "alpha",
                    "project_id": project_id,
                    "filename": "alpha.txt",
                    "property_type": "text",
                    "definition": "The Alpha design.",
                    "content": "Alpha uses CoreDB.",
                    "relative_path": "properties/alpha.txt",
                    "directory": "",
                    "embedding": [0.5],
                }
            ],
            [
                {
                    "id": "alpha-system",
                    "name": "Alpha System",
                    "definition": "A software system.",
                    "project_id": project_id,
                    "source_property_ids": ["alpha"],
                    "source_contexts": [
                        {"property_id": "alpha", "text": "Alpha uses CoreDB."}
                    ],
                    "embedding": [0.5],
                },
                {
                    "id": "coredb",
                    "name": "CoreDB",
                    "definition": "A database.",
                    "project_id": project_id,
                    "source_property_ids": ["alpha"],
                    "source_contexts": [
                        {"property_id": "alpha", "text": "Alpha uses CoreDB."}
                    ],
                    "embedding": [0.5],
                },
            ],
            [],
            [
                {
                    "source": "alpha-system",
                    "target": "coredb",
                    "type": "STORES_DATA_IN",
                }
            ],
        )
    )
    active_store.activate(project_id, "active-before-add")
    entity_document_sets = []
    entity_build_modes = []
    entity_only_modes = []
    entity_inventories = []
    embedding_batches = []
    extracted_paths = []
    agent_calls = {"dg": 0, "ga": 0}

    class RebuildingEntityBuilder:
        def __init__(self, *args, **kwargs):
            pass

        def build(self, documents, **kwargs):
            ids = [document["property_id"] for document in documents]
            entity_document_sets.append(ids)
            entity_build_modes.append(kwargs.get("incremental"))
            entity_only_modes.append(kwargs.get("entity_only"))
            entity_inventories.append(
                [item["id"] for item in kwargs.get("current_entities", [])]
            )
            if ids == ["beta"]:
                return (
                    [
                        {
                            "id": "beta-service",
                            "name": "Beta Service",
                            "definition": "An extension service.",
                            "project_id": project_id,
                            "source_property_ids": ["beta"],
                            "source_contexts": [
                                {
                                    "property_id": "beta",
                                    "text": "Beta extends Alpha.",
                                }
                            ],
                        },
                    ],
                    [
                        {
                            "source": "beta-service",
                            "target": "alpha-system",
                            "type": "EXTENDS",
                        }
                    ],
                )

        def propose_merges(self, pair):
            return []

        def generate_relation_edges(self, pair):
            if pair.right:
                return [
                    {
                        "source": "beta-service",
                        "target": "alpha-system",
                        "type": "EXTENDS",
                    }
                ]
            return []

    class StableGAAgent:
        def __init__(self, settings):
            pass

        def suggest_path(self, *args, **kwargs):
            agent_calls["ga"] += 1
            return ""

    class StableDGAgent:
        def __init__(self, settings):
            pass

        def generate(self, *args, **kwargs):
            agent_calls["dg"] += 1
            return type("Definition", (), {"definition": "The Beta extension."})()

    monkeypatch.setattr("backend.app.services.pipeline.GraphRAGBuilder", RebuildingEntityBuilder)
    monkeypatch.setattr("backend.app.services.pipeline.GAAgent", StableGAAgent)
    monkeypatch.setattr("backend.app.services.pipeline.DGAgent", StableDGAgent)
    class RecordingEmbedder:
        def embed(self, texts):
            embedding_batches.append(texts)
            return [[1.0] for _ in texts]

        def close(self):
            return None

    monkeypatch.setattr(
        "backend.app.services.pipeline.embedding_provider",
        lambda *_args, **_kwargs: RecordingEmbedder(),
    )
    monkeypatch.setattr(
        "backend.app.services.pipeline.extract_text",
        lambda path, _kind: extracted_paths.append(path.name)
        or path.read_text(encoding="utf-8"),
    )

    run_pipeline(
        settings,
        project_id,
        "beta",
        "add-job",
        "beta.txt",
        "text",
        second_path,
        operation="add",
        definition_override="The Beta extension.",
    )
    after_add = LocalGraphStore(settings).read(project_id)

    counts_before_remove = {
        "entity": len(entity_document_sets),
        "embedding": len(embedding_batches),
        **agent_calls,
    }
    run_property_removal(
        settings,
        project_id,
        "beta",
        "remove-job",
        second_path,
    )
    after_remove = LocalGraphStore(settings).read(project_id)

    assert entity_document_sets == [["beta"]]
    assert entity_build_modes == [None]
    assert entity_only_modes == [None]
    assert entity_inventories == [[]]
    assert after_add.property_edges == [
        {"source": "group:full-rebuild:/", "target": "alpha", "type": "CONTAINS_PROPERTY"},
        {"source": "group:full-rebuild:/", "target": "beta", "type": "CONTAINS_PROPERTY"},
    ]
    assert after_add.entity_edges == [
        {"source": "alpha-system", "target": "coredb", "type": "STORES_DATA_IN"},
        {"source": "beta-service", "target": "alpha-system", "type": "EXTENDS"},
    ]
    assert [item["id"] for item in after_add.entities] == [
        "alpha-system",
        "coredb",
        "beta-service",
    ]
    assert [item["content"] for item in after_add.properties] == [
        "Alpha uses CoreDB.",
        "Beta extends Alpha.",
    ]
    assert any("Beta extends Alpha." in batch for batch in embedding_batches)
    assert agent_calls == {"dg": 0, "ga": 1}
    assert counts_before_remove == {
        "entity": len(entity_document_sets),
        "embedding": len(embedding_batches),
        **agent_calls,
    }
    assert extracted_paths == ["beta.txt"]
    assert [item["id"] for item in after_remove.properties] == ["alpha"]
    assert after_remove.property_edges == [
        {"source": "group:full-rebuild:/", "target": "alpha", "type": "CONTAINS_PROPERTY"}
    ]
    assert after_remove.entity_edges == [
        {"source": "alpha-system", "target": "coredb", "type": "STORES_DATA_IN"}
    ]
    assert [item["id"] for item in after_remove.entities] == [
        "alpha-system",
        "coredb",
    ]
    assert catalog.get(project_id, "beta") is None
    assert not second_path.exists()


def test_rebuild_reuses_unchanged_property_content_and_embedding(
    tmp_path, monkeypatch
):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    project_id, property_id, job_id = "reuse-project", "beta", "reuse-job"
    property_root = settings.projects_dir / project_id / "properties"
    alpha_path = property_root / "alpha.txt"
    beta_path = property_root / "beta.txt"
    property_root.mkdir(parents=True)
    alpha_path.write_text("Alpha uses CoreDB.", encoding="utf-8")
    beta_path.write_text("Beta extends Alpha.", encoding="utf-8")
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES (?,?,?,?)",
            (project_id, "Reuse project", "now", "now"),
        )
        db.execute(
            "INSERT INTO jobs(id,project_id,stage,status,heartbeat) VALUES (?,?,?,?,?)",
            (job_id, project_id, "queued", "queued", "now"),
        )
    catalog = PropertyCatalog(settings)
    for item in (
        {
            "id": "alpha",
            "filename": "alpha.txt",
            "relative_path": "properties/alpha.txt",
            "definition": "The Alpha system.",
            "status": "active",
        },
        {
            "id": property_id,
            "filename": "beta.txt",
            "relative_path": "properties/beta.txt",
            "definition": None,
            "status": "queued",
        },
    ):
        catalog.create(
            project_id,
            {
                "project_id": project_id,
                "property_type": "text",
                "created_at": "now",
                "updated_at": "now",
                **item,
            },
        )
    active_store = LocalGraphStore(settings)
    embedding_route_signature = json.dumps(
        {"model": "stable-embedding"}, sort_keys=True, separators=(",", ":")
    )
    active_store.write_snapshot(
        GraphSnapshot(
            project_id,
            "active-before-reuse",
            [
                {
                    "id": "alpha",
                    "project_id": project_id,
                    "filename": "alpha.txt",
                    "property_type": "text",
                    "definition": "The Alpha system.",
                    "content": "Alpha uses CoreDB.",
                    "relative_path": "properties/alpha.txt",
                    "directory": "",
                    "embedding": [0.5, 0.5],
                    "_embedding_route_signature": embedding_route_signature,
                }
            ],
            [],
            [],
            [],
        )
    )
    active_store.activate(project_id, "active-before-reuse")

    extracted_paths = []
    embedding_batches = []

    def recording_extract(path, _kind):
        extracted_paths.append(path.name)
        return path.read_text(encoding="utf-8")

    class RecordingEmbedder:
        def embed(self, texts):
            embedding_batches.append(texts)
            return [[1.0, 0.0] for _ in texts]

        def close(self):
            return None

    class StableDGAgent:
        def __init__(self, settings):
            pass

        def generate(self, *args, **kwargs):
            return type("Definition", (), {"definition": "The Beta extension."})()

    class StableGAAgent:
        def __init__(self, settings):
            pass

        def suggest_path(self, *args, **kwargs):
            return ""

    class EmptyEntityBuilder:
        def __init__(self, *args, **kwargs):
            pass

        def build(self, documents, **kwargs):
            return [], []

    monkeypatch.setattr("backend.app.services.pipeline.extract_text", recording_extract)
    monkeypatch.setattr("backend.app.services.pipeline.DGAgent", StableDGAgent)
    monkeypatch.setattr("backend.app.services.pipeline.GAAgent", StableGAAgent)
    monkeypatch.setattr(
        "backend.app.services.pipeline.GraphRAGBuilder", EmptyEntityBuilder
    )
    monkeypatch.setattr(
        "backend.app.services.pipeline.embedding_provider",
        lambda *_args, **_kwargs: RecordingEmbedder(),
    )
    monkeypatch.setattr(
        "backend.app.services.pipeline.provider_route_metadata",
        lambda _settings: {
            "shared_embedding_route": {"model": "stable-embedding"}
        },
    )
    monkeypatch.setattr(
        "backend.app.services.graph_store.embedding_provider",
        lambda *_args, **_kwargs: RecordingEmbedder(),
    )

    run_pipeline(
        settings,
        project_id,
        property_id,
        job_id,
        "beta.txt",
        "text",
        beta_path,
        operation="add",
        definition_override="The Beta extension.",
    )

    snapshot = LocalGraphStore(settings).read(project_id)
    alpha = next(item for item in snapshot.properties if item["id"] == "alpha")
    assert extracted_paths == ["beta.txt"]
    assert embedding_batches == [["Beta extends Alpha."]]
    assert alpha["content"] == "Alpha uses CoreDB."
    assert alpha["embedding"] == [0.5, 0.5]


def test_pipeline_records_granular_graph_stage_timings(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, entity_agent_timeout_seconds=300)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    project_id, property_id, job_id = "timed-project", "timed-property", "timed-job"
    path = settings.projects_dir / project_id / "properties" / "notes.txt"
    path.parent.mkdir(parents=True)
    path.write_text("Atlas uses Neo4j.", encoding="utf-8")
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES (?,?,?,?)",
            (project_id, "Timed project", "now", "now"),
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
            "filename": "notes.txt",
            "property_type": "text",
            "relative_path": "properties/notes.txt",
            "definition": None,
            "status": "queued",
            "created_at": "now",
            "updated_at": "now",
        },
    )
    entity_provider_calls = []

    def recording_chat_provider(_settings, route_key=None, timeout=None):
        entity_provider_calls.append((route_key, timeout))
        return None

    monkeypatch.setattr(
        "backend.app.services.pipeline.chat_provider", recording_chat_provider
    )

    run_pipeline(
        settings,
        project_id,
        property_id,
        job_id,
        "notes.txt",
        "text",
        path,
        definition_override="Atlas deployment notes.",
    )

    with connect(settings.sqlite_path) as db:
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    timings = json.loads(job["timings_json"])
    assert list(timings) == [
        "queued",
        "dg-agent",
        "ga-agent",
        "graph-property-read",
        "graph-property-embedding",
        "graph-entity-generation",
        "graph-entity-merging",
        "graph-entity-relations",
        "graph-entity-embedding",
        "graph-snapshot",
        "graph-activate",
    ]
    assert all(value >= 0 for value in timings.values())
    assert job["stage"] == "active"
    assert job["stage_detail"] == "Candidate snapshot active"
    assert entity_provider_calls == [
        ("entity_agent_route", 300),
        ("entity_agent_route", 300),
        ("entity_agent_route", 300),
    ]


def test_transition_job_keeps_stage_start_time_for_progress_updates(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    project_id, job_id = "progress-project", "progress-job"
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES (?,?,?,?)",
            (project_id, "Progress project", "now", "now"),
        )
        db.execute(
            """
            INSERT INTO jobs(
                id,project_id,stage,status,heartbeat,stage_started_at,stage_detail,timings_json
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                job_id,
                project_id,
                "graph-property-relations",
                "running",
                "now",
                "2026-08-16T00:00:00+00:00",
                "Generating property relations: 40%",
                '{"graph-property-read":1.25}',
            ),
        )

    _transition_job(
        settings,
        job_id,
        "graph-property-relations",
        detail="Generating property relations: 60%",
    )

    with connect(settings.sqlite_path) as db:
        job = db.execute(
            "SELECT stage_started_at,stage_detail,timings_json FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    assert job["stage_started_at"] == "2026-08-16T00:00:00+00:00"
    assert job["stage_detail"] == "Generating property relations: 60%"
    assert json.loads(job["timings_json"]) == {"graph-property-read": 1.25}


def test_batch_pipeline_generates_entities_in_parallel_with_group_tree_graph(
    tmp_path, monkeypatch
):
    import backend.app.services.pipeline as pipeline_service

    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    project_id, job_id = "batch-project", "batch-job"
    property_root = settings.projects_dir / project_id / "properties"
    existing_path = property_root / "Existing" / "core.md"
    first_path = property_root / "atlas.md"
    second_path = property_root / "nova.md"
    existing_path.parent.mkdir(parents=True)
    existing_path.write_text("CoreDB stores project data.", encoding="utf-8")
    first_path.write_text("Atlas uses CoreDB.", encoding="utf-8")
    second_path.write_text("Nova integrates with Atlas.", encoding="utf-8")
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES (?,?,?,?)",
            (project_id, "Batch project", "now", "now"),
        )
        db.execute(
            "INSERT INTO jobs(id,project_id,stage,status,heartbeat) VALUES (?,?,?,?,?)",
            (job_id, project_id, "queued", "queued", "now"),
        )
        db.execute(
            "INSERT INTO project_locks(project_id,job_id,acquired_at) VALUES (?,?,?)",
            (project_id, job_id, "now"),
        )
    catalog = PropertyCatalog(settings)
    rows = [
        {
            "id": "core",
            "project_id": project_id,
            "filename": "core.md",
            "property_type": "markdown",
            "relative_path": "properties/Existing/core.md",
            "directory": "Existing",
            "definition": "The CoreDB storage guide.",
            "status": "active",
            "created_at": "now",
            "updated_at": "now",
        },
        {
            "id": "atlas",
            "project_id": project_id,
            "filename": "atlas.md",
            "property_type": "markdown",
            "relative_path": "properties/atlas.md",
            "definition": "The Atlas product guide.",
            "status": "queued",
            "created_at": "now",
            "updated_at": "now",
        },
        {
            "id": "nova",
            "project_id": project_id,
            "filename": "nova.md",
            "property_type": "markdown",
            "relative_path": "properties/nova.md",
            "definition": "The Nova integration guide.",
            "status": "queued",
            "created_at": "now",
            "updated_at": "now",
        },
    ]
    for row in rows:
        catalog.create(project_id, row)

    active_store = LocalGraphStore(settings)
    active_store.write_snapshot(
        GraphSnapshot(
            project_id,
            "before-batch",
            [
                {
                    "id": "core",
                    "project_id": project_id,
                    "filename": "core.md",
                    "property_type": "markdown",
                    "definition": "The CoreDB storage guide.",
                    "content": "CoreDB stores project data.",
                    "relative_path": "properties/Existing/core.md",
                    "directory": "Existing",
                    "embedding": [0.5],
                }
            ],
            [
                {
                    "id": "coredb",
                    "name": "CoreDB",
                    "definition": "A project data store.",
                    "project_id": project_id,
                    "source_property_ids": ["core"],
                    "source_contexts": [
                        {
                            "property_id": "core",
                            "text": "CoreDB stores project data.",
                        }
                    ],
                    "embedding": [0.5],
                }
            ],
            [],
            [],
        )
    )
    active_store.activate(project_id, "before-batch")

    ga_calls = []
    entity_calls = []
    merge_calls = []
    relation_calls = []
    progress_updates = []
    entity_barrier = Barrier(2, timeout=3)
    calls_lock = Lock()
    original_transition_job = pipeline_service._transition_job

    def recording_transition_job(*args, **kwargs):
        stage = args[2] if len(args) > 2 else kwargs.get("stage")
        detail = kwargs.get("detail")
        if stage in {
            "graph-entity-generation",
            "graph-entity-merging",
            "graph-entity-relations",
        }:
            progress_updates.append((stage, detail))
        return original_transition_job(*args, **kwargs)

    class RecordingGAAgent:
        def __init__(self, settings):
            self.settings = settings

        def organize_tree(self, tree_context, import_contexts):
            ga_calls.append((tree_context, import_contexts))
            raise AssertionError("confirmed import plans must skip Group Arrangement Agent")

    class RecordingEntityBuilder:
        def __init__(self, *args, **kwargs):
            pass

        def build(self, documents, **kwargs):
            property_id = documents[0]["property_id"]
            entity_barrier.wait()
            with calls_lock:
                entity_calls.append(
                    {
                        "documents": [item["property_id"] for item in documents],
                        "text": documents[0]["text"],
                        "original_text": documents[0].get("original_text"),
                        "has_offsets": bool(documents[0].get("extraction_chunks")),
                        "inventory": [
                            item["id"] for item in kwargs.get("current_entities", [])
                        ],
                        "incremental": kwargs.get("incremental"),
                        "entity_only": kwargs.get("entity_only"),
                    }
                )
            if property_id == "atlas":
                return [
                    {
                        "id": "atlas-product",
                        "name": "Atlas",
                        "definition": "A product that uses CoreDB.",
                        "project_id": project_id,
                        "source_property_ids": ["atlas"],
                        "source_contexts": [
                            {
                                "property_id": "atlas",
                                "text": "Atlas uses CoreDB.",
                            }
                        ],
                    }
                ]
            return [
                {
                    "id": "nova-product",
                    "name": "Nova",
                    "definition": "An integration for Atlas.",
                    "project_id": project_id,
                    "source_property_ids": ["nova"],
                    "source_contexts": [
                        {
                            "property_id": "nova",
                            "text": "Nova integrates with Atlas.",
                        }
                    ],
                }
            ]

        def propose_merges(self, pair):
            merge_calls.append(
                (
                    [item["id"] for item in pair.left],
                    [item["id"] for item in pair.right],
                )
            )
            return []

        def generate_relation_edges(self, pair):
            left_ids = [item["id"] for item in pair.left]
            right_ids = [item["id"] for item in pair.right]
            relation_calls.append((left_ids, right_ids))
            if right_ids == []:
                return [
                    {
                        "source": "nova-product",
                        "target": "atlas-product",
                        "type": "INTEGRATES_WITH",
                    }
                ]
            return [
                {"source": "atlas-product", "target": "coredb", "type": "USES"}
            ]

    class RecordingEmbedder:
        def embed(self, texts):
            return [[1.0] for _ in texts]

        def close(self):
            return None

    monkeypatch.setattr(pipeline_service, "GAAgent", RecordingGAAgent)
    monkeypatch.setattr(pipeline_service, "GraphRAGBuilder", RecordingEntityBuilder)
    monkeypatch.setattr(
        pipeline_service, "_transition_job", recording_transition_job
    )
    monkeypatch.setattr(
        pipeline_service,
        "embedding_provider",
        lambda *_args, **_kwargs: RecordingEmbedder(),
    )
    monkeypatch.setattr(
        pipeline_service,
        "chat_provider",
        lambda *_args, **_kwargs: None,
    )

    pipeline_service.run_batch_pipeline(
        settings,
        project_id,
        job_id,
        [
            {
                "property_id": "atlas",
                "filename": "atlas.md",
                "kind": "markdown",
                "path": first_path,
                "definition": "The Atlas product guide.",
                "comment": "Product documentation",
                "directory": "Products/Atlas",
            },
            {
                "property_id": "nova",
                "filename": "nova.md",
                "kind": "markdown",
                "path": second_path,
                "definition": "The Nova integration guide.",
                "comment": "Integration documentation",
                "directory": "Products/Nova",
            },
        ],
    )

    assert ga_calls == []
    assert sorted(entity_calls, key=lambda item: item["documents"]) == [
        {
            "documents": ["atlas"],
            "text": "Atlas uses CoreDB.",
            "original_text": "Atlas uses CoreDB.",
            "has_offsets": False,
            "inventory": [],
            "incremental": None,
            "entity_only": None,
        },
        {
            "documents": ["nova"],
            "text": "Nova integrates with Atlas.",
            "original_text": "Nova integrates with Atlas.",
            "has_offsets": False,
            "inventory": [],
            "incremental": None,
            "entity_only": None,
        },
    ]
    generation_details = [
        detail
        for stage, detail in progress_updates
        if stage == "graph-entity-generation"
    ]
    assert generation_details[0] == "Generating entity nodes: 0%"
    assert generation_details[-1] == "Generating entity nodes: 100%"
    assert ("graph-entity-relations", "Generating relations for entities: 100%") in progress_updates
    assert len(merge_calls) == 2
    assert relation_calls == [
        (["atlas-product", "nova-product"], []),
        (["atlas-product", "nova-product"], ["coredb"]),
    ]
    assert not (
        settings.projects_dir
        / project_id
        / "jobs"
        / "extraction-text"
    ).exists()
    snapshot = LocalGraphStore(settings).read(project_id)
    assert [item["id"] for item in snapshot.entities] == [
        "coredb",
        "atlas-product",
        "nova-product",
    ]
    assert snapshot.entity_edges == [
        {
            "source": "nova-product",
            "target": "atlas-product",
            "type": "INTEGRATES_WITH",
        },
        {"source": "atlas-product", "target": "coredb", "type": "USES"},
    ]
    assert snapshot.property_edges == [
        {"source": "group:batch-project:/", "target": "group:batch-project:Existing", "type": "CONTAINS_GROUP"},
        {"source": "group:batch-project:/", "target": "group:batch-project:Products", "type": "CONTAINS_GROUP"},
        {"source": "group:batch-project:Products", "target": "group:batch-project:Products/Atlas", "type": "CONTAINS_GROUP"},
        {"source": "group:batch-project:Products", "target": "group:batch-project:Products/Nova", "type": "CONTAINS_GROUP"},
        {"source": "group:batch-project:Existing", "target": "core", "type": "CONTAINS_PROPERTY"},
        {"source": "group:batch-project:Products/Atlas", "target": "atlas", "type": "CONTAINS_PROPERTY"},
        {"source": "group:batch-project:Products/Nova", "target": "nova", "type": "CONTAINS_PROPERTY"},
    ]
    active_rows = {row["id"]: row for row in catalog.list(project_id)}
    assert active_rows["atlas"]["relative_path"] == (
        "properties/Products/Atlas/atlas.md"
    )
    assert active_rows["nova"]["relative_path"] == (
        "properties/Products/Nova/nova.md"
    )
    assert active_rows["atlas"]["status"] == "active"
    assert active_rows["nova"]["status"] == "active"
    with connect(settings.sqlite_path) as db:
        job = db.execute("SELECT status,progress_json FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert job["status"] == "completed"
        assert json.loads(job["progress_json"])["completed_property_ids"] == [
            "atlas",
            "nova",
        ]
        assert db.execute(
            "SELECT COUNT(*) FROM project_locks WHERE project_id=?", (project_id,)
        ).fetchone()[0] == 0


def test_batch_retry_relates_completed_entities_recovered_from_checkpoint(
    tmp_path, monkeypatch
):
    import backend.app.services.pipeline as pipeline_service

    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    project_id, job_id = "retry-relations-project", "retry-relations-job"
    property_root = settings.projects_dir / project_id / "properties"
    property_root.mkdir(parents=True)
    paths = {
        "core": property_root / "core.md",
        "atlas": property_root / "atlas.md",
        "nova": property_root / "nova.md",
    }
    contents = {
        "core": "CoreDB stores project data.",
        "atlas": "Atlas uses CoreDB.",
        "nova": "Nova integrates with Atlas.",
    }
    for property_id, path in paths.items():
        path.write_text(contents[property_id], encoding="utf-8")
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES (?,?,?,?)",
            (project_id, "Retry relations project", "now", "now"),
        )
        db.execute(
            "INSERT INTO jobs(id,project_id,stage,status,heartbeat) VALUES (?,?,?,?,?)",
            (job_id, project_id, "queued", "queued", "now"),
        )
        db.execute(
            "INSERT INTO project_locks(project_id,job_id,acquired_at) VALUES (?,?,?)",
            (project_id, job_id, "now"),
        )
    catalog = PropertyCatalog(settings)
    for property_id in ("core", "atlas", "nova"):
        catalog.create(
            project_id,
            {
                "id": property_id,
                "project_id": project_id,
                "filename": paths[property_id].name,
                "property_type": "markdown",
                "relative_path": f"properties/{paths[property_id].name}",
                "directory": "",
                "definition": f"The {property_id} guide.",
                "status": "active" if property_id == "core" else "failed",
                "created_at": "now",
                "updated_at": "now",
            },
        )

    baseline_property = {
        "id": "core",
        "project_id": project_id,
        "filename": "core.md",
        "property_type": "markdown",
        "definition": "The core guide.",
        "content": contents["core"],
        "relative_path": "properties/core.md",
        "directory": "",
        "embedding": [0.5],
    }
    baseline_entity = {
        "id": "coredb",
        "name": "CoreDB",
        "definition": "A project data store.",
        "project_id": project_id,
        "source_property_ids": ["core"],
        "source_contexts": [
            {"property_id": "core", "text": contents["core"]}
        ],
        "embedding": [0.5],
    }
    checkpoint_properties = [
        baseline_property,
        {
            **baseline_property,
            "id": "atlas",
            "filename": "atlas.md",
            "definition": "The atlas guide.",
            "content": contents["atlas"],
            "relative_path": "properties/atlas.md",
        },
        {
            **baseline_property,
            "id": "nova",
            "filename": "nova.md",
            "definition": "The nova guide.",
            "content": contents["nova"],
            "relative_path": "properties/nova.md",
        },
    ]
    checkpoint_entities = [
        {
            **baseline_entity,
            "source_property_ids": ["core", "atlas"],
            "source_contexts": [
                {"property_id": "core", "text": contents["core"]},
                {"property_id": "atlas", "text": contents["atlas"]},
            ],
        },
        {
            "id": "atlas-product",
            "name": "Atlas",
            "definition": "A product that uses CoreDB.",
            "project_id": project_id,
            "source_property_ids": ["atlas"],
            "source_contexts": [
                {"property_id": "atlas", "text": contents["atlas"]}
            ],
        },
        {
            "id": "nova-product",
            "name": "Nova",
            "definition": "An integration for Atlas.",
            "project_id": project_id,
            "source_property_ids": ["nova"],
            "source_contexts": [
                {"property_id": "nova", "text": contents["nova"]}
            ],
        },
    ]
    store = LocalGraphStore(settings)
    store.write_snapshot(
        GraphSnapshot(
            project_id,
            "before-retry",
            [baseline_property],
            [baseline_entity],
            [],
            [],
        )
    )
    store.activate(project_id, "before-retry")
    store.write_snapshot(
        GraphSnapshot(
            project_id,
            "entity-checkpoint",
            checkpoint_properties,
            checkpoint_entities,
            [],
            [],
        )
    )

    merge_calls = []
    relation_calls = []
    progress_updates = []
    original_transition_job = pipeline_service._transition_job

    def recording_transition_job(*args, **kwargs):
        stage = args[2] if len(args) > 2 else kwargs.get("stage")
        if stage in {
            "graph-entity-generation",
            "graph-entity-merging",
            "graph-entity-relations",
        }:
            progress_updates.append((stage, kwargs.get("detail")))
        return original_transition_job(*args, **kwargs)

    class RetryEntityBuilder:
        def __init__(self, *args, **kwargs):
            pass

        def build(self, *_args, **_kwargs):
            raise AssertionError("completed entity generation must not run again")

        def propose_merges(self, pair):
            merge_calls.append(
                ([item["id"] for item in pair.left], [item["id"] for item in pair.right])
            )
            return []

        def generate_relation_edges(self, pair):
            left_ids = [item["id"] for item in pair.left]
            right_ids = [item["id"] for item in pair.right]
            relation_calls.append((left_ids, right_ids))
            if right_ids:
                return [
                    {"source": "atlas-product", "target": "coredb", "type": "USES"}
                ]
            return [
                {
                    "source": "nova-product",
                    "target": "atlas-product",
                    "type": "INTEGRATES_WITH",
                }
            ]

    class RecordingEmbedder:
        def embed(self, texts):
            return [[1.0] for _ in texts]

        def close(self):
            return None

    monkeypatch.setattr(pipeline_service, "GraphRAGBuilder", RetryEntityBuilder)
    monkeypatch.setattr(
        pipeline_service, "_transition_job", recording_transition_job
    )
    monkeypatch.setattr(
        pipeline_service,
        "embedding_provider",
        lambda *_args, **_kwargs: RecordingEmbedder(),
    )
    monkeypatch.setattr(
        pipeline_service, "chat_provider", lambda *_args, **_kwargs: None
    )

    pipeline_service.run_batch_pipeline(
        settings,
        project_id,
        job_id,
        [
            {
                "property_id": property_id,
                "filename": paths[property_id].name,
                "kind": "markdown",
                "path": paths[property_id],
                "definition": f"The {property_id} guide.",
                "comment": "",
                "directory": "",
            }
            for property_id in ("atlas", "nova")
        ],
        resume_snapshot_id="entity-checkpoint",
        completed_property_ids=["atlas", "nova"],
        directories={"atlas": "", "nova": ""},
    )

    assert merge_calls == [
        (["atlas-product", "nova-product"], []),
        (["atlas-product", "nova-product"], ["coredb"]),
    ]
    assert relation_calls == merge_calls
    assert (
        "graph-entity-generation",
        "Generating entity nodes: 100%",
    ) in progress_updates
    assert (
        "graph-entity-merging",
        "Generating redundant entity merge proposals: 0%",
    ) in progress_updates
    assert (
        "graph-entity-merging",
        "Generating redundant entity merge proposals: 100%",
    ) in progress_updates
    assert ("graph-entity-relations", "Generating relations for entities: 0%") in progress_updates
    assert ("graph-entity-relations", "Generating relations for entities: 100%") in progress_updates
    assert any(
        stage == "graph-entity-generation"
        and detail == "Generating entity nodes: 100%"
        for stage, detail in progress_updates
    )
    snapshot = LocalGraphStore(settings).read(project_id)
    assert [item["id"] for item in snapshot.entities] == [
        "coredb",
        "atlas-product",
        "nova-product",
    ]
    assert snapshot.entities[0]["source_property_ids"] == ["core", "atlas"]
    assert snapshot.entities[0]["source_contexts"] == [
        {"property_id": "core", "text": contents["core"]},
        {"property_id": "atlas", "text": contents["atlas"]},
    ]
    assert snapshot.entity_edges == [
        {
            "source": "nova-product",
            "target": "atlas-product",
            "type": "INTEGRATES_WITH",
        },
        {"source": "atlas-product", "target": "coredb", "type": "USES"},
    ]


def test_job_heartbeat_stays_fresh_during_blocking_provider_call(
    tmp_path, monkeypatch
):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES (?,?,?,?)",
            ("heartbeat-project", "Heartbeat project", "now", "now"),
        )
        db.execute(
            "INSERT INTO jobs(id,project_id,stage,status,heartbeat) VALUES (?,?,?,?,?)",
            ("heartbeat-job", "heartbeat-project", "graph-entity-extraction", "running", "old"),
        )
    monkeypatch.setattr(
        "backend.app.services.pipeline.JOB_HEARTBEAT_SECONDS", 0.01
    )

    with _job_heartbeat(settings, "heartbeat-job"):
        time.sleep(0.04)

    with connect(settings.sqlite_path) as db:
        heartbeat = db.execute(
            "SELECT heartbeat FROM jobs WHERE id='heartbeat-job'"
        ).fetchone()["heartbeat"]
    assert heartbeat != "old"
