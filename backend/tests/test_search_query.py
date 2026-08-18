import json
from types import SimpleNamespace

from backend.app.api import query as query_api
from backend.app.services.llm import AnswerLLM
from backend.app.services.providers import ProviderError
from backend.app.services.retrieval import GraphRetriever, Retriever, RetrievalConfig


class StaticGraphStore:
    def __init__(self, property_graph=None, entity_graph=None):
        self.graphs = {
            "property": property_graph or {"nodes": [], "edges": []},
            "entity": entity_graph or {"nodes": [], "edges": []},
        }

    def graph(self, project_id, kind):
        assert project_id == "project"
        return self.graphs[kind]

    def neighbors(self, project_id, kind, node_ids):
        graph = self.graph(project_id, kind)
        frontier = set(node_ids)
        edges = [
            edge
            for edge in graph["edges"]
            if edge["source"] in frontier or edge["target"] in frontier
        ]
        adjacent = frontier | {
            endpoint
            for edge in edges
            for endpoint in (edge["source"], edge["target"])
        }
        return {
            "nodes": [node for node in graph["nodes"] if node["id"] in adjacent],
            "edges": edges,
        }


def graph_retriever(store, **overrides):
    values = {
        "minimum_direct_score": 0.3,
        "minimum_neighbor_score": 0.2,
        "max_seed_nodes_per_kind": 4,
        "max_nodes": 16,
        **overrides,
    }
    config = RetrievalConfig(**values)
    return GraphRetriever(store, config=config, embed_query=lambda _query: [1.0, 0.0])


def test_graph_retriever_drops_arbitrary_top_k_results_below_the_threshold():
    store = StaticGraphStore(
        property_graph={
            "nodes": [
                {
                    "id": "finance",
                    "filename": "finance.xlsx",
                    "definition": "Quarterly revenue figures.",
                    "embedding": [0.0, 1.0],
                }
            ],
            "edges": [],
        }
    )

    result = graph_retriever(store).search("project", "Atlas deployment")

    assert result["properties"] == []
    assert result["entities"] == []


def test_graph_retriever_applies_independent_property_entity_and_total_caps():
    properties = [
        {
            "id": f"property-{index}",
            "filename": f"atlas-property-{index}.md",
            "definition": "Atlas reference.",
            "embedding": [1.0, 0.0],
        }
        for index in range(8)
    ]
    entities = [
        {
            "id": f"entity-{index}",
            "name": f"Atlas Entity {index}",
            "definition": "An Atlas entity.",
            "embedding": [1.0, 0.0],
            "source_property_ids": [],
        }
        for index in range(8)
    ]
    retriever = graph_retriever(
        StaticGraphStore(
            property_graph={"nodes": properties, "edges": []},
            entity_graph={"nodes": entities, "edges": []},
        ),
        max_seed_nodes_per_kind=20,
    )

    per_kind = retriever.search(
        "project",
        "Atlas",
        property_limit=2,
        entity_limit=3,
        total_limit=10,
    )
    total_bounded = retriever.context(
        "project",
        "Atlas",
        property_limit=8,
        entity_limit=8,
        total_limit=5,
    )

    assert len(per_kind["properties"]) == 2
    assert len(per_kind["entities"]) == 3
    assert (
        len(total_bounded["properties"]) + len(total_bounded["entities"])
        == 5
    )


def test_search_endpoint_uses_the_configured_search_limits(monkeypatch):
    recorded = {}

    class FakeRetriever:
        def __init__(self, _store):
            pass

        def search(self, project_id, query, **kwargs):
            recorded.update(kwargs)
            return {"properties": [], "entities": []}

    monkeypatch.setattr(query_api, "get_project", lambda *_args: {"id": "project"})
    monkeypatch.setattr(query_api, "Neo4jGraphStore", lambda _settings: object())
    monkeypatch.setattr(query_api, "GraphRetriever", FakeRetriever)
    monkeypatch.setattr(
        query_api,
        "load_retrieval_limits",
        lambda _settings: SimpleNamespace(
            search_property_limit=31,
            search_entity_limit=32,
            ai_query_property_limit=15,
            ai_query_entity_limit=15,
            ai_query_total_node_limit=30,
        ),
        raising=False,
    )

    query_api.search(
        "project",
        query_api.QueryRequest(query="Atlas"),
        settings=object(),
        user={
            "capabilities": {"search.properties", "search.entities"},
        },
    )

    assert recorded == {
        "allowed_kinds": {"property", "entity"},
        "property_limit": 31,
        "entity_limit": 32,
        "total_limit": 63,
    }


def test_ai_query_injects_only_the_configured_bounded_context(monkeypatch):
    recorded = {}
    empty_context = {
        "properties": [],
        "entities": [],
        "relations": [],
        "retrieval_paths": [],
    }

    class FakeRetriever:
        def __init__(self, _store):
            pass

        def context(self, project_id, query, **kwargs):
            recorded.update(kwargs)
            return empty_context

    class FakeHistoryStore:
        def __init__(self, _settings):
            pass

        def list(self, _project_id, _user_id):
            return []

        def append_exchange(self, *_args, **_kwargs):
            pass

    class FakeAnswerLLM:
        def __init__(self, **_kwargs):
            pass

        def answer(self, _query, context, history=None):
            assert context is empty_context
            assert history == []
            return {"answer": "Bounded answer", "citations": []}

    monkeypatch.setattr(query_api, "get_project", lambda *_args: {"id": "project"})
    monkeypatch.setattr(query_api, "Neo4jGraphStore", lambda _settings: object())
    monkeypatch.setattr(query_api, "GraphRetriever", FakeRetriever)
    monkeypatch.setattr(query_api, "QueryHistoryStore", FakeHistoryStore)
    monkeypatch.setattr(query_api, "AnswerLLM", FakeAnswerLLM)
    monkeypatch.setattr(
        query_api,
        "load_retrieval_limits",
        lambda _settings: SimpleNamespace(
            search_property_limit=30,
            search_entity_limit=30,
            ai_query_property_limit=12,
            ai_query_entity_limit=13,
            ai_query_total_node_limit=20,
        ),
        raising=False,
    )

    query_api.ai_query(
        "project",
        query_api.QueryRequest(query="Atlas"),
        settings=SimpleNamespace(
            neo4j_property_database="property",
            neo4j_entity_database="entity",
        ),
        user={"id": "user"},
    )

    assert recorded == {
        "property_limit": 12,
        "entity_limit": 13,
        "total_limit": 20,
    }


def test_ai_query_constructs_project_scoped_toolbox(monkeypatch):
    captured = {}
    context = {
        "properties": [],
        "entities": [],
        "relations": [],
        "retrieval_paths": [],
    }

    class FakeStore:
        pass

    class FakeRetriever:
        def __init__(self, store):
            captured["retriever_store"] = store

        def context(self, project_id, query, **kwargs):
            return context

    class FakeCatalog:
        def __init__(self, settings):
            captured["catalog_settings"] = settings

    class FakeToolbox:
        def __init__(self, project_id, store, catalog):
            captured["toolbox"] = self
            captured["toolbox_project_id"] = project_id
            captured["toolbox_store"] = store
            captured["toolbox_catalog"] = catalog

    class FakeHistoryStore:
        def __init__(self, settings):
            pass

        def list(self, project_id, user_id):
            return []

        def append_exchange(self, *args, **kwargs):
            pass

    class FakeAnswerLLM:
        def __init__(self, *, settings, toolbox):
            captured["answer_settings"] = settings
            captured["answer_toolbox"] = toolbox

        def answer(self, question, supplied_context, history=None):
            return {"answer": "Ready", "citations": []}

    settings = SimpleNamespace(
        neo4j_property_database="properties",
        neo4j_entity_database="entities",
    )
    store = FakeStore()
    monkeypatch.setattr(query_api, "get_project", lambda *_args: {"id": "project"})
    monkeypatch.setattr(query_api, "Neo4jGraphStore", lambda _settings: store)
    monkeypatch.setattr(query_api, "GraphRetriever", FakeRetriever)
    monkeypatch.setattr(query_api, "PropertyCatalog", FakeCatalog, raising=False)
    monkeypatch.setattr(query_api, "AIQueryTools", FakeToolbox, raising=False)
    monkeypatch.setattr(query_api, "QueryHistoryStore", FakeHistoryStore)
    monkeypatch.setattr(query_api, "AnswerLLM", FakeAnswerLLM)
    monkeypatch.setattr(
        query_api,
        "load_retrieval_limits",
        lambda _settings: SimpleNamespace(
            ai_query_property_limit=15,
            ai_query_entity_limit=15,
            ai_query_total_node_limit=30,
        ),
    )

    query_api.ai_query(
        "project",
        query_api.QueryRequest(query="Atlas"),
        settings=settings,
        user={"id": "user"},
    )

    assert captured["retriever_store"] is store
    assert captured["toolbox_project_id"] == "project"
    assert captured["toolbox_store"] is store
    assert captured["answer_toolbox"] is captured["toolbox"]


def test_ai_query_stream_constructs_project_scoped_toolbox(monkeypatch):
    captured = {}
    context = {
        "properties": [],
        "entities": [],
        "relations": [],
        "retrieval_paths": [],
    }

    class FakeStore:
        pass

    class FakeRetriever:
        def __init__(self, store):
            self.store = store

        def context(self, project_id, query, **kwargs):
            return context

    class FakeToolbox:
        def __init__(self, project_id, store, catalog):
            captured["toolbox_instance"] = self
            captured["project_id"] = project_id
            captured["store"] = store
            captured["catalog"] = catalog

    class FakeHistoryStore:
        def __init__(self, settings):
            pass

        def list(self, project_id, user_id):
            return []

        def append_exchange(self, *args, **kwargs):
            pass

    class FakeAnswerLLM:
        def __init__(self, *, settings, toolbox):
            captured["toolbox"] = toolbox

        def stream_answer(self, question, supplied_context, history=None):
            return {"chunks": iter(["Ready"]), "citations": []}

    settings = SimpleNamespace(
        neo4j_property_database="properties",
        neo4j_entity_database="entities",
    )
    store = FakeStore()
    catalog = object()
    monkeypatch.setattr(query_api, "get_project", lambda *_args: {"id": "project"})
    monkeypatch.setattr(query_api, "Neo4jGraphStore", lambda _settings: store)
    monkeypatch.setattr(query_api, "GraphRetriever", FakeRetriever)
    monkeypatch.setattr(query_api, "PropertyCatalog", lambda _settings: catalog, raising=False)
    monkeypatch.setattr(query_api, "AIQueryTools", FakeToolbox, raising=False)
    monkeypatch.setattr(query_api, "QueryHistoryStore", FakeHistoryStore)
    monkeypatch.setattr(query_api, "AnswerLLM", FakeAnswerLLM)
    monkeypatch.setattr(
        query_api,
        "load_retrieval_limits",
        lambda _settings: SimpleNamespace(
            ai_query_property_limit=15,
            ai_query_entity_limit=15,
            ai_query_total_node_limit=30,
        ),
    )

    query_api.ai_query_stream(
        "project",
        query_api.QueryRequest(query="Atlas"),
        settings=settings,
        user={"id": "user"},
    )

    assert captured["project_id"] == "project"
    assert captured["store"] is store
    assert captured["catalog"] is catalog
    assert captured["toolbox"] is captured["toolbox_instance"]


def test_graph_retriever_keeps_disconnected_relevant_seeds_and_expands_typed_edges():
    store = StaticGraphStore(
        entity_graph={
            "nodes": [
                {
                    "id": "atlas",
                    "name": "Atlas",
                    "definition": "A deployment product.",
                    "embedding": [1.0, 0.0],
                    "source_property_ids": [],
                },
                {
                    "id": "neo4j",
                    "name": "Neo4j",
                    "definition": "A graph database platform.",
                    "embedding": [0.0, 1.0],
                    "source_property_ids": [],
                },
                {
                    "id": "atlas-team",
                    "name": "Atlas Team",
                    "definition": "The team responsible for Atlas deployment.",
                    "embedding": [1.0, 0.0],
                    "source_property_ids": [],
                },
                {
                    "id": "finance",
                    "name": "Finance",
                    "definition": "Quarterly revenue.",
                    "embedding": [0.0, 1.0],
                    "source_property_ids": [],
                },
            ],
            "edges": [
                {"source": "atlas", "target": "neo4j", "type": "USES"},
            ],
        }
    )

    result = graph_retriever(store).search("project", "Atlas deployment")

    by_id = {item["id"]: item for item in result["entities"]}
    assert by_id["atlas"]["retrieval_reason"] == "Direct match"
    assert by_id["atlas-team"]["retrieval_reason"] == "Direct match"
    assert by_id["neo4j"]["retrieval_reason"] == "Related through USES"
    assert by_id["neo4j"]["retrieval_path"] == ["Atlas", "USES", "Neo4j"]
    assert "finance" not in by_id
    assert result["relations"] == [
        {
            "source": "atlas",
            "source_label": "Atlas",
            "source_kind": "entity",
            "type": "USES",
            "target": "neo4j",
            "target_label": "Neo4j",
            "target_kind": "entity",
        }
    ]


def test_graph_retriever_bridges_an_entity_seed_to_its_source_property():
    store = StaticGraphStore(
        property_graph={
            "nodes": [
                {
                    "id": "manual",
                    "filename": "manual.md",
                    "definition": "A general installation manual.",
                    "embedding": [0.0, 1.0],
                }
            ],
            "edges": [],
        },
        entity_graph={
            "nodes": [
                {
                    "id": "atlas",
                    "name": "Atlas",
                    "definition": "A deployment product.",
                    "embedding": [1.0, 0.0],
                    "source_property_ids": ["manual"],
                }
            ],
            "edges": [],
        },
    )

    result = graph_retriever(store).context("project", "Atlas")

    assert result["properties"][0]["retrieval_reason"] == (
        "Related through EXTRACTED_FROM"
    )
    assert result["properties"][0]["retrieval_path"] == [
        "Atlas",
        "EXTRACTED_FROM",
        "manual.md",
    ]
    assert result["retrieval_paths"][0]["path"]


def test_graph_retriever_uses_two_hops_only_through_a_strong_non_hub_relation():
    graph = {
        "nodes": [
            {"id": "atlas", "name": "Atlas", "embedding": [1.0, 0.0]},
            {"id": "neo4j", "name": "Neo4j", "embedding": [0.0, 1.0]},
            {"id": "cypher", "name": "Cypher", "embedding": [0.0, 1.0]},
        ],
        "edges": [
            {"source": "atlas", "target": "neo4j", "type": "USES"},
            {"source": "neo4j", "target": "cypher", "type": "SUPPORTS"},
        ],
    }

    result = graph_retriever(StaticGraphStore(entity_graph=graph)).search(
        "project", "Atlas"
    )

    cypher = next(item for item in result["entities"] if item["id"] == "cypher")
    assert cypher["retrieval_path"] == [
        "Atlas",
        "USES",
        "Neo4j",
        "SUPPORTS",
        "Cypher",
    ]


def test_graph_retriever_does_not_traverse_a_high_degree_intermediate_hub():
    graph = {
        "nodes": [
            {"id": "atlas", "name": "Atlas", "embedding": [1.0, 0.0]},
            {"id": "hub", "name": "Platform", "embedding": [0.0, 1.0]},
            {"id": "one", "name": "One", "embedding": [0.0, 1.0]},
            {"id": "two", "name": "Two", "embedding": [0.0, 1.0]},
        ],
        "edges": [
            {"source": "atlas", "target": "hub", "type": "USES"},
            {"source": "hub", "target": "one", "type": "CONTAINS"},
            {"source": "hub", "target": "two", "type": "CONTAINS"},
        ],
    }

    result = graph_retriever(
        StaticGraphStore(entity_graph=graph), generic_hub_degree=2
    ).search("project", "Atlas")

    assert {item["id"] for item in result["entities"]} == {"atlas", "hub"}


def test_graph_retriever_marks_an_incoming_edge_direction_in_the_path():
    graph = {
        "nodes": [
            {"id": "atlas", "name": "Atlas", "embedding": [0.0, 1.0]},
            {"id": "neo4j", "name": "Neo4j", "embedding": [1.0, 0.0]},
        ],
        "edges": [{"source": "atlas", "target": "neo4j", "type": "USES"}],
    }

    result = graph_retriever(StaticGraphStore(entity_graph=graph)).search(
        "project", "Neo4j"
    )

    atlas = next(item for item in result["entities"] if item["id"] == "atlas")
    assert atlas["retrieval_reason"] == "Related through USES"
    assert atlas["retrieval_path"] == ["Neo4j", "USES (incoming)", "Atlas"]


def test_retriever_groups_properties_and_entities_without_answer_generation():
    AnswerLLM.calls = 0
    store = StaticGraphStore(
        property_graph={
            "nodes": [{"id": "p", "filename": "question.md", "embedding": [1.0, 0.0]}],
            "edges": [],
        },
        entity_graph={
            "nodes": [{"id": "e", "name": "Question", "embedding": [1.0, 0.0]}],
            "edges": [],
        },
    )

    result = graph_retriever(store).search("project", "question")
    assert [item["id"] for item in result["properties"]] == ["p"]
    assert [item["id"] for item in result["entities"]] == ["e"]
    assert AnswerLLM.calls == 0


def test_retriever_enriches_matched_properties_with_incident_relations():
    store = StaticGraphStore(
        property_graph={
            "nodes": [
                {"id": "manual", "filename": "manual.docx", "content": "Atlas installation and usage guide.", "embedding": [1.0, 0.0]},
                {"id": "architecture", "filename": "architecture.pdf", "embedding": [0.0, 1.0]},
                {"id": "employees", "filename": "employees.xlsx", "embedding": [0.0, 1.0]},
                {"id": "finance", "filename": "finance.xlsx", "embedding": [0.0, 1.0]},
            ],
            "edges": [
                {"source": "manual", "target": "architecture", "type": "DOCUMENTS_PRODUCT"},
                {"source": "employees", "target": "manual", "type": "SUPPORTS_TRAINING"},
                {"source": "employees", "target": "finance", "type": "UNRELATED_EDGE"},
            ],
        }
    )

    result = graph_retriever(store).search("project", "Atlas guide")

    assert result["properties"][0]["content"] == (
        "Atlas installation and usage guide."
    )
    assert result["properties"][0]["relations"] == [
        {
            "source": "manual",
            "target": "architecture",
            "type": "DOCUMENTS_PRODUCT",
            "source_filename": "manual.docx",
            "target_filename": "architecture.pdf",
            "direction": "outgoing",
            "related_property_id": "architecture",
            "related_property_filename": "architecture.pdf",
        },
        {
            "source": "employees",
            "target": "manual",
            "type": "SUPPORTS_TRAINING",
            "source_filename": "employees.xlsx",
            "target_filename": "manual.docx",
            "direction": "incoming",
            "related_property_id": "employees",
            "related_property_filename": "employees.xlsx",
        },
    ]


def test_ai_query_context_contains_deduplicated_property_relations():
    store = StaticGraphStore(
        property_graph={
            "nodes": [
                {"id": "one", "filename": "one.md", "content": "An explanation.", "embedding": [1.0, 0.0]},
                {"id": "two", "filename": "two.pdf", "content": "Two", "embedding": [0.0, 1.0]},
            ],
            "edges": [{"source": "one", "target": "two", "type": "EXPLAINS"}],
        }
    )

    context = graph_retriever(store).context("project", "explanation")

    assert context["property_relations"] == [
        {
            "source": "one",
            "target": "two",
            "type": "EXPLAINS",
            "source_filename": "one.md",
            "target_filename": "two.pdf",
        }
    ]


def test_ai_query_answer_has_citations_from_both_graphs():
    result = AnswerLLM().answer("where?", {"properties": [{"id": "p", "filename": "one.md"}], "entities": [{"id": "e", "name": "Neo4j"}]})
    assert {item["kind"] for item in result["citations"]} == {"property", "entity"}


def test_ai_query_citations_explain_direct_and_graph_related_evidence():
    context = {
        "properties": [
            {
                "id": "manual",
                "filename": "manual.md",
                "retrieval_reason": "Related through EXTRACTED_FROM",
                "retrieval_path": ["Atlas", "EXTRACTED_FROM", "manual.md"],
            }
        ],
        "entities": [
            {
                "id": "atlas",
                "name": "Atlas",
                "retrieval_reason": "Direct match",
                "retrieval_path": ["Atlas"],
            }
        ],
    }

    citations = AnswerLLM._citations(context)

    assert citations == [
        {
            "kind": "property",
            "id": "manual",
            "label": "manual.md",
            "reason": "Related through EXTRACTED_FROM",
            "path": ["Atlas", "EXTRACTED_FROM", "manual.md"],
        },
        {
            "kind": "entity",
            "id": "atlas",
            "label": "Atlas",
            "reason": "Direct match",
            "path": ["Atlas"],
        },
    ]


def test_ai_query_prompt_contains_only_the_bounded_evidence_graph():
    context = {
        "properties": [{"id": "manual", "filename": "manual.md", "definition": "Atlas manual."}],
        "entities": [{"id": "atlas", "name": "Atlas", "definition": "A product."}],
        "relations": [
            {
                "source": "atlas",
                "source_label": "Atlas",
                "source_kind": "entity",
                "type": "EXTRACTED_FROM",
                "target": "manual",
                "target_label": "manual.md",
                "target_kind": "property",
            }
        ],
        "retrieval_paths": [
            {
                "seed": "Atlas",
                "target": "manual.md",
                "path": ["Atlas", "EXTRACTED_FROM", "manual.md"],
                "score": 0.62,
            }
        ],
        "property_graph": {"nodes": [{"id": "must-not-leak"}], "edges": []},
        "entity_graph": {"nodes": [{"id": "must-not-leak"}], "edges": []},
    }

    prompt = json.loads(AnswerLLM._messages("What is Atlas?", context)[-1]["content"])

    assert prompt["relations"] == context["relations"]
    assert prompt["retrieval_paths"] == context["retrieval_paths"]
    assert "property_graph" not in prompt
    assert "entity_graph" not in prompt


def test_ai_query_sends_the_complete_conversation_history_to_the_provider():
    class Provider:
        def __init__(self):
            self.messages = None

        def complete(self, messages, **kwargs):
            self.messages = messages
            return "The first question was about multi-agent projects."

    history = [
        {"role": "user", "content": "Which projects use multiple agents?"},
        {"role": "assistant", "content": "Hatsume uses multiple agents."},
    ]
    provider = Provider()

    AnswerLLM(provider=provider).answer(
        "What did I just ask?",
        {"properties": [], "entities": []},
        history=history,
    )

    assert provider.messages[1:3] == history
    assert json.loads(provider.messages[3]["content"])["question"] == "What did I just ask?"


def test_ai_query_request_accepts_conversation_history():
    payload = query_api.QueryRequest(
        query="What did I just ask?",
        history=[
            {"role": "user", "content": "Which projects use multiple agents?"},
            {"role": "assistant", "content": "Hatsume uses multiple agents."},
        ],
    )

    assert [message.model_dump() for message in payload.history] == [
        {"role": "user", "content": "Which projects use multiple agents?"},
        {"role": "assistant", "content": "Hatsume uses multiple agents."},
    ]


def test_ai_query_stream_emits_both_graph_citations_and_answer_deltas():
    context = {
        "properties": [{"id": "p", "filename": "one.md"}],
        "entities": [{"id": "e", "name": "Neo4j"}],
    }

    class FakeAnswerLLM:
        def stream_answer(self, question, supplied_context, history=None):
            assert question == "where?"
            assert supplied_context == context
            assert history is None
            return {
                "chunks": iter(["Answer", " text"]),
                "citations": [
                    {"kind": "property", "id": "p", "label": "one.md"},
                    {"kind": "entity", "id": "e", "label": "Neo4j"},
                ],
            }

    events = [
        json.loads(line)
        for line in query_api.ai_query_events(
            "where?",
            context,
            FakeAnswerLLM(),
            ["property_graph", "entity_graph"],
        )
    ]

    assert events[0]["type"] == "sources"
    assert {item["kind"] for item in events[0]["citations"]} == {
        "property",
        "entity",
    }
    assert events[0]["retrieved"] == {
        "properties": 1,
        "entities": 1,
        "relations": 0,
        "retrieval_paths": 0,
        "databases": ["property_graph", "entity_graph"],
    }
    assert events[1:3] == [
        {"type": "delta", "content": "Answer"},
        {"type": "delta", "content": " text"},
    ]
    assert events[-1] == {"type": "done"}


def test_ai_query_stream_emits_late_sources_when_detail_tools_add_citations():
    citations = [{"kind": "property", "id": "manual", "label": "manual.md"}]

    class FakeAnswerLLM:
        def stream_answer(self, question, supplied_context, history=None):
            def chunks():
                citations.append(
                    {
                        "kind": "entity",
                        "id": "atlas",
                        "label": "Atlas",
                        "reason": "Inspected by AI Query",
                    }
                )
                yield "Atlas is documented."

            return {"chunks": chunks(), "citations": citations}

    events = [
        json.loads(line)
        for line in query_api.ai_query_events(
            "What is Atlas?",
            {"properties": [], "entities": []},
            FakeAnswerLLM(),
            ["property_graph", "entity_graph"],
        )
    ]

    assert [event["type"] for event in events] == [
        "sources",
        "delta",
        "sources",
        "done",
    ]
    assert events[0]["citations"] == [
        {"kind": "property", "id": "manual", "label": "manual.md"}
    ]
    assert events[-2]["citations"][-1] == {
        "kind": "entity",
        "id": "atlas",
        "label": "Atlas",
        "reason": "Inspected by AI Query",
    }


def test_ai_query_stream_reports_the_completed_answer_for_persistence():
    completed = []
    citations = [{"kind": "entity", "id": "atlas", "label": "Atlas"}]

    class FakeAnswerLLM:
        def stream_answer(self, question, supplied_context, history=None):
            return {"chunks": iter(["Atlas", " is ready."]), "citations": citations}

    events = list(
        query_api.ai_query_events(
            "Is Atlas ready?",
            {"properties": [], "entities": []},
            FakeAnswerLLM(),
            ["property_graph", "entity_graph"],
            on_complete=lambda answer, sources: completed.append((answer, sources)),
        )
    )

    assert json.loads(events[-1]) == {"type": "done"}
    assert completed == [("Atlas is ready.", citations)]


def test_ai_query_stream_does_not_persist_a_failed_response():
    completed = []

    class FakeAnswerLLM:
        def stream_answer(self, question, supplied_context, history=None):
            def chunks():
                yield "Partial"
                raise ProviderError("provider failed")

            return {"chunks": chunks(), "citations": []}

    events = [
        json.loads(line)
        for line in query_api.ai_query_events(
            "Will this finish?",
            {"properties": [], "entities": []},
            FakeAnswerLLM(),
            ["property_graph", "entity_graph"],
            on_complete=lambda answer, sources: completed.append((answer, sources)),
        )
    ]

    assert events[-1] == {"type": "error", "message": "provider failed"}
    assert completed == []
