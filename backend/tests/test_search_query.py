import json

from backend.app.api import query as query_api
from backend.app.services.llm import AnswerLLM
from backend.app.services.providers import ProviderError
from backend.app.services.retrieval import Retriever


def test_retriever_groups_properties_and_entities_without_answer_generation():
    AnswerLLM.calls = 0
    calls = []

    class Store:
        def search(self, project_id, query, kind, limit):
            calls.append((project_id, query, kind, limit))
            if kind == "properties":
                return [{"kind": "property", "id": "p"}]
            if kind == "entities":
                return [{"kind": "entity", "id": "e"}]
            return []

        def graph(self, project_id, kind):
            assert project_id == "project"
            return {"nodes": [], "edges": [], "snapshot_id": "active"}

    result = Retriever(Store()).search("project", "question")
    assert [item["id"] for item in result["properties"]] == ["p"]
    assert [item["id"] for item in result["entities"]] == ["e"]
    assert [call[2] for call in calls] == ["properties", "entities"]
    assert AnswerLLM.calls == 0


def test_retriever_enriches_matched_properties_with_incident_relations():
    class Store:
        def search(self, project_id, query, kind, limit):
            if kind == "properties":
                return [
                    {
                        "kind": "property",
                        "id": "manual",
                        "filename": "manual.docx",
                        "content": "Atlas installation and usage guide.",
                    }
                ]
            return []

        def graph(self, project_id, kind):
            assert kind == "property"
            return {
                "nodes": [
                    {"id": "manual", "filename": "manual.docx"},
                    {"id": "architecture", "filename": "architecture.pdf"},
                    {"id": "employees", "filename": "employees.xlsx"},
                    {"id": "finance", "filename": "finance.xlsx"},
                ],
                "edges": [
                    {
                        "source": "manual",
                        "target": "architecture",
                        "type": "DOCUMENTS_PRODUCT",
                    },
                    {
                        "source": "employees",
                        "target": "manual",
                        "type": "SUPPORTS_TRAINING",
                    },
                    {
                        "source": "employees",
                        "target": "finance",
                        "type": "UNRELATED_EDGE",
                    },
                ],
                "snapshot_id": "active",
            }

    result = Retriever(Store()).search("project", "Atlas guide")

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
    class Store:
        def search(self, project_id, query, kind, limit):
            if kind == "properties":
                return [
                    {"id": "one", "filename": "one.md", "content": "One"},
                    {"id": "two", "filename": "two.pdf", "content": "Two"},
                ]
            return []

        def graph(self, project_id, kind):
            graph = {
                "nodes": [
                    {"id": "one", "filename": "one.md"},
                    {"id": "two", "filename": "two.pdf"},
                ],
                "edges": [
                    {"source": "one", "target": "two", "type": "EXPLAINS"}
                ],
                "snapshot_id": "active",
            }
            return graph if kind == "property" else {"nodes": [], "edges": []}

    context = Retriever(Store()).context("project", "explanation")

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
        "databases": ["property_graph", "entity_graph"],
    }
    assert events[1:3] == [
        {"type": "delta", "content": "Answer"},
        {"type": "delta", "content": " text"},
    ]
    assert events[-1] == {"type": "done"}


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
