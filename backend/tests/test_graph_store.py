import pytest

from backend.app.config import Settings
from backend.app.db import initialize
from backend.app.services.graph_store import GraphSnapshot, LocalGraphStore, entity_extraction_chunks, prune_property_snapshot
from backend.app.services.graph_store import embedding


def test_prune_property_snapshot_removes_owned_nodes_edges_and_shared_sources(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    store = LocalGraphStore(settings)
    store.write_snapshot(GraphSnapshot(
        "project",
        "before",
        [
            {"id": "remove", "filename": "remove.md"},
            {"id": "keep", "filename": "keep.md"},
        ],
        [
            {"id": "owned", "source_property_ids": ["remove"], "source_contexts": [{"property_id": "remove", "text": "Owned context"}]},
            {"id": "shared", "source_property_ids": ["remove", "keep"], "source_contexts": [{"property_id": "remove", "text": "Old"}, {"property_id": "keep", "text": "Keep"}]},
            {"id": "unrelated", "source_property_ids": ["keep"], "source_contexts": [{"property_id": "keep", "text": "Other"}]},
        ],
        [
            {"source": "remove", "target": "keep", "type": "REFERENCES"},
        ],
        [
            {"source": "owned", "target": "shared", "type": "USES"},
            {"source": "shared", "target": "unrelated", "type": "SUPPORTS"},
        ],
    ))
    store.activate("project", "before")

    pruned = prune_property_snapshot(store, "project", "remove", "after")

    assert [node["id"] for node in pruned.properties] == ["keep"]
    assert pruned.property_edges == []
    assert [node["id"] for node in pruned.entities] == ["shared", "unrelated"]
    shared = pruned.entities[0]
    assert shared["source_property_ids"] == ["keep"]
    assert shared["source_contexts"] == [{"property_id": "keep", "text": "Keep"}]
    assert pruned.entity_edges == [
        {"source": "shared", "target": "unrelated", "type": "SUPPORTS"}
    ]


@pytest.mark.parametrize(
    ("length", "expected_lengths"),
    [
        (12_000, [12_000]),
        (12_001, [12_000, 501]),
        (23_500, [12_000, 12_000]),
        (24_000, [12_000, 12_000, 1_000]),
    ],
)
def test_entity_extraction_chunk_boundaries_cover_all_content(length, expected_lengths):
    content = "".join(chr(65 + index % 26) for index in range(length))
    chunks = entity_extraction_chunks(content)

    assert [len(chunk) for chunk in chunks] == expected_lengths
    assert chunks[0] == content[:12_000]
    for index, chunk in enumerate(chunks[1:], start=1):
        start = index * 11_500
        assert chunk == content[start : start + 12_000]


def test_long_content_embeddings_are_batched_and_combined(monkeypatch):
    import backend.app.services.graph_store as graph_store

    calls = []

    class Embedder:
        def embed(self, texts):
            calls.append(texts)
            return [[1.0, 0.0], [0.0, 1.0]]

    monkeypatch.setattr(graph_store, "embedding_provider", lambda *_args, **_kwargs: Embedder())

    vector = graph_store.embedding_for_settings(
        "A" * (graph_store.EMBEDDING_CHUNK_CHARS + 1), object()
    )

    assert [len(text) for text in calls[0]] == [
        graph_store.EMBEDDING_CHUNK_CHARS,
        1,
    ]
    assert vector == pytest.approx([2**-0.5, 2**-0.5])


def test_multiple_embedding_inputs_share_one_provider_batch(monkeypatch):
    import backend.app.services.graph_store as graph_store

    calls = []

    class Embedder:
        def embed(self, texts):
            calls.append(texts)
            return [
                [1.0, 0.0],
                [0.0, 1.0],
                [0.0, 2.0],
            ]

        def close(self):
            return None

    monkeypatch.setattr(
        graph_store, "embedding_provider", lambda *_args, **_kwargs: Embedder()
    )

    vectors = graph_store.embeddings_for_settings(
        ["A" * (graph_store.EMBEDDING_CHUNK_CHARS + 1), "B"], object()
    )

    assert [len(text) for text in calls[0]] == [
        graph_store.EMBEDDING_CHUNK_CHARS,
        1,
        1,
    ]
    assert len(calls) == 1
    assert vectors[0] == pytest.approx([2**-0.5, 2**-0.5])
    assert vectors[1] == [0.0, 2.0]


def test_property_search_matches_words_from_stored_content(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    store = LocalGraphStore(settings)
    store.write_snapshot(
        GraphSnapshot(
            "project",
            "content-search",
            [
                {
                    "id": "manual",
                    "filename": "manual.docx",
                    "definition": "A product guide.",
                    "content": "Install Atlas with the desktop deployment wizard.",
                    "embedding": embedding(
                        "Install Atlas with the desktop deployment wizard."
                    ),
                },
                {
                    "id": "revenue",
                    "filename": "revenue.xlsx",
                    "definition": "A finance workbook.",
                    "content": "Quarterly revenue and operating margin.",
                    "embedding": embedding(
                        "Quarterly revenue and operating margin."
                    ),
                },
            ],
            [],
            [],
            [],
        )
    )
    store.activate("project", "content-search")

    results = store.search("project", "desktop deployment", "properties")

    assert results[0]["id"] == "manual"
    assert results[0]["content"] == (
        "Install Atlas with the desktop deployment wizard."
    )


def test_property_graph_contains_image_nodes_and_two_named_graphs_are_distinct(tmp_path):
    settings = type("Settings", (), {"data_dir": tmp_path, "allow_local_fallback": True, "neo4j_uri": "bolt://invalid", "neo4j_user": "neo4j", "neo4j_password": "x", "neo4j_property_database": "property_graph", "neo4j_entity_database": "entity_graph"})()
    store = LocalGraphStore(settings)
    store.write_snapshot(GraphSnapshot("p", "s", [{"id": "img", "property_type": "image"}], [{"id": "entity", "name": "Neo4j"}], [], []))
    store.activate("p", "s")
    assert store.graph("p", "property")["nodes"][0]["property_type"] == "image"
    assert store.graph("p", "entity")["nodes"][0]["name"] == "Neo4j"


def test_candidate_graph_can_be_read_before_activation(tmp_path):
    settings = type("Settings", (), {"data_dir": tmp_path})()
    store = LocalGraphStore(settings)
    store.write_snapshot(GraphSnapshot("p", "active", [{"id": "old"}], [], [], []))
    store.activate("p", "active")
    store.write_snapshot(GraphSnapshot("p", "candidate", [{"id": "new"}], [{"id": "entity"}], [{"source": "new", "target": "new", "type": "RELATED"}], []))

    candidate = store.graph("p", "property", snapshot_id="candidate")
    assert candidate["snapshot_id"] == "candidate"
    assert candidate["nodes"] == [{"id": "new"}]
    assert candidate["edges"][0]["type"] == "RELATED"


def test_local_graph_store_returns_only_the_requested_neighbor_frontier(tmp_path):
    settings = type("Settings", (), {"data_dir": tmp_path})()
    store = LocalGraphStore(settings)
    store.write_snapshot(
        GraphSnapshot(
            "p",
            "neighbors",
            [
                {"id": "manual", "filename": "manual.md"},
                {"id": "architecture", "filename": "architecture.md"},
                {"id": "finance", "filename": "finance.xlsx"},
            ],
            [],
            [
                {"source": "manual", "target": "architecture", "type": "DOCUMENTS"},
                {"source": "finance", "target": "architecture", "type": "FUNDS"},
            ],
            [],
        )
    )
    store.activate("p", "neighbors")

    neighborhood = store.neighbors("p", "property", ["manual"])

    assert [node["id"] for node in neighborhood["nodes"]] == [
        "manual",
        "architecture",
    ]
    assert neighborhood["edges"] == [
        {"source": "manual", "target": "architecture", "type": "DOCUMENTS"}
    ]


def test_legacy_co_occurs_entity_edge_is_typed_from_mention_context(tmp_path):
    settings = type("Settings", (), {"data_dir": tmp_path})()
    store = LocalGraphStore(settings)
    entities = [
        {"id": "atlas", "name": "Atlas", "source_contexts": [{"property_id": "p1", "text": "Atlas uses Neo4j."}]},
        {"id": "neo4j", "name": "Neo4j", "source_contexts": [{"property_id": "p1", "text": "Atlas uses Neo4j."}]},
    ]
    store.write_snapshot(GraphSnapshot("p", "legacy", [], entities, [], [{"source": "atlas", "target": "neo4j", "type": "CO_OCCURS"}]))
    store.activate("p", "legacy")

    graph = store.graph("p", "entity")
    assert graph["edges"] == [{"source": "atlas", "target": "neo4j", "type": "USES"}]


def test_neo4j_store_defers_active_graph_replacement_until_snapshot_activation(tmp_path):
    calls = []

    class Session:
        def __init__(self, database):
            self.database = database

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def run(self, query, **params):
            calls.append((self.database, query, params))

    class Driver:
        def session(self, database):
            return Session(database)

    settings = type("Settings", (), {"neo4j_property_database": "property_graph", "neo4j_entity_database": "entity_graph"})()
    store = object.__new__(__import__("backend.app.services.graph_store", fromlist=["Neo4jGraphStore"]).Neo4jGraphStore)
    store.settings = settings
    store.driver = Driver()
    store.local = LocalGraphStore(type("LocalSettings", (), {"data_dir": tmp_path})())
    snapshot = GraphSnapshot("p", "candidate", [{"id": "p1", "project_id": "p", "embedding": [1.0]}], [{"id": "e1", "project_id": "p"}], [], [])
    store.write_snapshot(snapshot)
    assert any("CandidateProperty" in query for _, query, _ in calls)
    assert not any("MATCH (p:Property {project_id: $project}) DETACH DELETE p" in query for _, query, _ in calls)
    store.activate("p", "candidate")
    assert any("MATCH (p:Property {project_id: $project}) DETACH DELETE p" in query for _, query, _ in calls)


def test_neo4j_search_uses_vector_index_with_shared_query_embedding(monkeypatch, tmp_path):
    calls = []

    class Session:
        def __init__(self, database):
            self.database = database

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def run(self, query, **params):
            calls.append((self.database, query, params))
            if "db.index.vector.queryNodes" in query:
                return [{"node": {"id": "p1", "project_id": "p"}, "score": 0.91}]
            return []

    class Driver:
        def session(self, database):
            return Session(database)

    import backend.app.services.graph_store as graph_store
    monkeypatch.setattr(graph_store, "embedding_for_settings", lambda *_args: [0.2, 0.4])
    settings = type("Settings", (), {"neo4j_property_database": "property_graph", "neo4j_entity_database": "entity_graph"})()
    store = object.__new__(graph_store.Neo4jGraphStore)
    store.settings = settings
    store.driver = Driver()
    store.local = LocalGraphStore(type("LocalSettings", (), {"data_dir": tmp_path})())
    assert store.search("p", "release plan", "properties") == [{"kind": "property", "id": "p1", "project_id": "p", "score": 0.91}]
    assert any("db.index.vector.queryNodes" in query and params["index_name"] == "property_embedding" for _, query, params in calls)
