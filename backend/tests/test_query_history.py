import hashlib
import json

from backend.app.config import Settings
from backend.app.api import query as query_api
from backend.app.services.query_history import QueryHistoryStore


def test_query_history_survives_new_store_instance_and_is_scoped_by_project_and_user(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    first_store = QueryHistoryStore(settings)
    citations = [{"kind": "property", "id": "manual", "label": "manual.md"}]

    first_store.append_exchange(
        "project-a",
        "user-a",
        "What is Atlas?",
        "Atlas is a product.",
        citations,
    )
    first_store.append_exchange(
        "project-a",
        "user-b",
        "What is Orion?",
        "Orion is another product.",
        [],
    )
    first_store.append_exchange(
        "project-b",
        "user-a",
        "What is in this project?",
        "A separate project.",
        [],
    )

    restarted_store = QueryHistoryStore(settings)

    assert restarted_store.list("project-a", "user-a") == [
        {"role": "user", "content": "What is Atlas?"},
        {
            "role": "assistant",
            "content": "Atlas is a product.",
            "citations": citations,
        },
    ]
    assert restarted_store.list("project-a", "user-b")[0]["content"] == "What is Orion?"
    assert restarted_store.list("project-b", "user-a")[0]["content"] == "What is in this project?"

    user_key = hashlib.sha256(b"user-a").hexdigest()
    history_path = settings.projects_dir / "project-a" / "query-history" / f"{user_key}.json"
    assert history_path.is_file()
    assert json.loads(history_path.read_text())["messages"][1]["citations"] == citations
    assert not (settings.conf_dir / "query-history").exists()


def test_clear_only_removes_the_selected_users_project_history(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    store = QueryHistoryStore(settings)
    for project_id, user_id in [
        ("project-a", "user-a"),
        ("project-a", "user-b"),
        ("project-b", "user-a"),
    ]:
        store.append_exchange(project_id, user_id, "Question", "Answer", [])

    store.clear("project-a", "user-a")

    assert store.list("project-a", "user-a") == []
    assert store.list("project-a", "user-b")
    assert store.list("project-b", "user-a")


def test_query_history_preserves_citation_retrieval_paths(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    store = QueryHistoryStore(settings)
    citation = {
        "kind": "entity",
        "id": "neo4j",
        "label": "Neo4j",
        "reason": "Related through USES",
        "path": ["Atlas", "USES", "Neo4j"],
    }

    store.append_exchange("project", "user", "What does Atlas use?", "Neo4j.", [citation])

    assert store.list("project", "user")[1]["citations"] == [citation]


def test_query_history_persists_compaction_while_retaining_full_ui_history(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    store = QueryHistoryStore(settings)
    store.append_exchange(
        "project", "user", "Where is Atlas documented?", "In manual.md.", []
    )
    original_history = store.list("project", "user")

    store.save_compaction(
        "project",
        "user",
        original_history,
        "The user asked where Atlas is documented; the answer was manual.md.",
    )
    store.append_exchange(
        "project", "user", "What format is it?", "Markdown.", []
    )

    full_history = store.list("project", "user")
    assert len(full_history) == 4
    assert store.cached_compaction("project", "user", full_history) == {
        "message_count": 2,
        "summary": "The user asked where Atlas is documented; the answer was manual.md.",
    }


def test_ai_query_history_context_compacts_once_then_reuses_cached_summary(
    tmp_path,
):
    settings = Settings(data_dir=tmp_path / "data")
    store = QueryHistoryStore(settings)
    store.append_exchange("project", "user", "x" * 500, "A long answer.", [])

    class Compactor:
        provider = object()

        def __init__(self):
            self.calls = 0

        def compact_history(self, history):
            self.calls += 1
            assert history[0]["content"] == "x" * 500
            return "Earlier context summary."

    compactor = Compactor()
    history = query_api._llm_history(store.list("project", "user"))

    first_context = query_api._history_context(
        store, "project", "user", history, compactor, 100
    )
    store.append_exchange("project", "user", "Follow up?", "Yes.", [])
    extended_history = query_api._llm_history(store.list("project", "user"))
    second_context = query_api._history_context(
        store, "project", "user", extended_history, compactor, 100
    )

    assert compactor.calls == 1
    assert first_context == [
        {
            "role": "assistant",
            "content": "Compacted earlier conversation context:\nEarlier context summary.",
        }
    ]
    assert second_context == [
        first_context[0],
        {"role": "user", "content": "Follow up?"},
        {"role": "assistant", "content": "Yes."},
    ]


def test_history_api_uses_the_authenticated_user_id(monkeypatch, tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    store = QueryHistoryStore(settings)
    store.append_exchange("project-a", "user-a", "Question A", "Answer A", [])
    store.append_exchange("project-a", "user-b", "Question B", "Answer B", [])
    monkeypatch.setattr(query_api, "get_project", lambda *_: {"id": "project-a"})

    response = query_api.get_ai_query_history(
        "project-a", settings=settings, user={"id": "user-a"}
    )
    query_api.clear_ai_query_history(
        "project-a", settings=settings, user={"id": "user-a"}
    )

    assert response["messages"][0]["content"] == "Question A"
    assert store.list("project-a", "user-a") == []
    assert store.list("project-a", "user-b")[0]["content"] == "Question B"
