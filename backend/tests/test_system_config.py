import json
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.config import Settings, get_settings
from backend.app.db import connect, initialize
from backend.app.seed import seed_defaults
from backend.app.services import graph_store
from backend.app.services.graph_store import DEFAULT_ENTITY_PROMPT, PREVIOUS_CONCISE_DEFINITION_ENTITY_PROMPT, PREVIOUS_DYNAMIC_RELATION_ENTITY_PROMPT, PREVIOUS_FIXED_RELATION_ENTITY_PROMPT
from backend.app.services.providers import chat_provider


def _headers(client: TestClient) -> dict[str, str]:
    token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_import_provider_readiness_reports_each_missing_import_route(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    seed_defaults(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            llm = client.post(
                "/api/system/providers",
                json={
                    "name": "Import LLM",
                    "provider_type": "llm",
                    "model": "chat-model",
                    "base_url": "https://provider.test/v1",
                    "secret": "llm-secret",
                },
                headers=headers,
            ).json()
            embedding = client.post(
                "/api/system/providers",
                json={
                    "name": "Import Embedding",
                    "provider_type": "embedding",
                    "model": "embedding-model",
                    "base_url": "https://provider.test/v1",
                    "secret": "embedding-secret",
                },
                headers=headers,
            ).json()
            initially_missing = client.get(
                "/api/system/import-provider-readiness",
                headers=headers,
            )
            assert [
                route["label"]
                for route in initially_missing.json()["missing_routes"]
            ] == [
                "Definition Generation Agent",
                "Group Arrangement Agent",
                "Entity Extraction Agent",
                "Shared Embedding Model",
            ]
            client.patch(
                "/api/system/config",
                json={
                    "dg_agent_route": llm["id"],
                    "ga_agent_route": llm["id"],
                },
                headers=headers,
            ).raise_for_status()

            incomplete = client.get(
                "/api/system/import-provider-readiness",
                headers=headers,
            )

            assert incomplete.status_code == 200
            assert incomplete.json() == {
                "ready": False,
                "missing_routes": [
                    {
                        "key": "entity_agent_route",
                        "label": "Entity Extraction Agent",
                        "provider_type": "llm",
                    },
                    {
                        "key": "shared_embedding_route",
                        "label": "Shared Embedding Model",
                        "provider_type": "embedding",
                    },
                ],
                "can_configure": True,
            }
            assert "secret" not in incomplete.text

            client.patch(
                "/api/system/config",
                json={
                    "entity_agent_route": llm["id"],
                    "shared_embedding_route": embedding["id"],
                    "ai_query_route": None,
                },
                headers=headers,
            ).raise_for_status()

            complete = client.get(
                "/api/system/import-provider-readiness",
                headers=headers,
            )
            assert complete.json() == {
                "ready": True,
                "missing_routes": [],
                "can_configure": True,
            }
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_provider_profiles_never_return_secret_and_routes_require_existing_profile():
    with TestClient(app) as client:
        headers = _headers(client)
        profile = client.post("/api/system/providers", json={"name": f"Local LLM {uuid4().hex}", "provider_type": "llm", "model": "local-model", "secret": "do-not-render"}, headers=headers)
        assert profile.status_code == 201
        assert "secret" not in profile.json()
        assert profile.json()["secret_configured"] is True
        listed = client.get("/api/system/providers", headers=headers).json()
        item = next(row for row in listed if row["id"] == profile.json()["id"])
        assert "secret" not in item
        assert item["secret_configured"] is True
        invalid = client.patch("/api/system/config", json={"dg_agent_route": "missing-provider"}, headers=headers)
        assert invalid.status_code == 422
        valid = client.patch("/api/system/config", json={"dg_agent_route": profile.json()["id"]}, headers=headers)
        assert valid.status_code == 200
        assert valid.json()["routes"]["dg_agent_route"] == profile.json()["id"]
        entity_route = client.patch("/api/system/config", json={"entity_agent_route": profile.json()["id"]}, headers=headers)
        assert entity_route.status_code == 200
        assert entity_route.json()["routes"]["entity_agent_route"] == profile.json()["id"]


def test_system_readiness_reports_local_fallback_without_neo4j_credentials():
    with TestClient(app) as client:
        headers = _headers(client)
        neo4j = client.get("/api/system/neo4j/check", headers=headers)
        storage = client.get("/api/system/storage/check", headers=headers)
        assert neo4j.status_code == 200
        assert neo4j.json()["fallback"] is True
        assert storage.status_code == 200
        assert storage.json()["writable"] is True


def test_default_entity_schema_includes_an_entity_definition_field():
    with TestClient(app) as client:
        config = client.get("/api/system/config", headers=_headers(client))
        assert config.status_code == 200
        assert "definition" in config.json()["entity_schema"]
        assert "description" not in config.json()["entity_schema"]
        prompt = config.json()["entity_prompt"]
        assert "meaning is consistent" in prompt
        assert "specific, identifiable entities" in prompt
        assert "Independent subgraphs and isolated entities" in prompt
        assert "never connect by co-occurrence" in prompt
        assert "specific relation type" in prompt
        assert "one plain sentence" in prompt
        assert "under 25 words" in prompt
        assert "Do not copy source text, code, logs, or Markdown" in prompt
        assert "Do not extract every noun" in prompt
        assert "generic concepts (user, code, network)" in prompt
        assert "people, organizations, products, technologies, places, laws, standards" in prompt
        assert "English ASCII id" in prompt
        assert "original Unicode spelling" in prompt
        assert "readable English word combination" in prompt
        assert "personal-resume" in prompt
        assert "staff-management-system" in prompt


def test_seed_upgrades_previous_concise_definition_entity_prompt(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    previous_prompt = PREVIOUS_CONCISE_DEFINITION_ENTITY_PROMPT
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO system_config(key,value,updated_at) VALUES ('entity_prompt',?,'now')",
            (previous_prompt,),
        )

    seed_defaults(settings)

    with connect(settings.sqlite_path) as db:
        stored = db.execute(
            "SELECT value FROM system_config WHERE key='entity_prompt'",
        ).fetchone()["value"]
    assert stored == DEFAULT_ENTITY_PROMPT
    assert "Do not extract every noun" in stored


def test_seed_upgrades_previous_selection_entity_prompt(tmp_path):
    from backend.app.services.graph_store import PREVIOUS_SELECTION_ENTITY_PROMPT

    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO system_config(key,value,updated_at) VALUES ('entity_prompt',?,'now')",
            (PREVIOUS_SELECTION_ENTITY_PROMPT,),
        )

    seed_defaults(settings)

    with connect(settings.sqlite_path) as db:
        stored = db.execute(
            "SELECT value FROM system_config WHERE key='entity_prompt'",
        ).fetchone()["value"]
    assert stored == DEFAULT_ENTITY_PROMPT
    assert "English ASCII id" in stored
    assert "original Unicode spelling" in stored


def test_seed_upgrades_the_previous_full_default_to_the_compact_entity_prompt(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    previous_prompt = getattr(
        graph_store, "PREVIOUS_DEFAULT_ENTITY_PROMPT", "missing-legacy-prompt"
    )
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO system_config(key,value,updated_at) VALUES ('entity_prompt',?,'now')",
            (previous_prompt,),
        )

    seed_defaults(settings)

    with connect(settings.sqlite_path) as db:
        stored = db.execute(
            "SELECT value FROM system_config WHERE key='entity_prompt'"
        ).fetchone()["value"]
    assert previous_prompt != "missing-legacy-prompt"
    assert stored == graph_store.DEFAULT_ENTITY_PROMPT
    assert len(stored) <= 2_600


def test_seed_upgrades_the_intermediate_compact_entity_prompt(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    previous_prompt = getattr(
        graph_store, "PREVIOUS_COMPACT_ENTITY_PROMPT", "missing-compact-prompt"
    )
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO system_config(key,value,updated_at) VALUES ('entity_prompt',?,'now')",
            (previous_prompt,),
        )

    seed_defaults(settings)

    with connect(settings.sqlite_path) as db:
        stored = db.execute(
            "SELECT value FROM system_config WHERE key='entity_prompt'"
        ).fetchone()["value"]
    assert previous_prompt != "missing-compact-prompt"
    assert stored == graph_store.DEFAULT_ENTITY_PROMPT
    assert "people, organizations, products, technologies" in stored


def test_seed_upgrades_the_previous_entity_identifier_prompt(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    previous_prompt = getattr(
        graph_store, "PREVIOUS_ENTITY_IDENTIFIER_PROMPT", "missing-id-prompt"
    )
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO system_config(key,value,updated_at) VALUES ('entity_prompt',?,'now')",
            (previous_prompt,),
        )

    seed_defaults(settings)

    with connect(settings.sqlite_path) as db:
        stored = db.execute(
            "SELECT value FROM system_config WHERE key='entity_prompt'"
        ).fetchone()["value"]
    assert previous_prompt != "missing-id-prompt"
    assert stored == graph_store.DEFAULT_ENTITY_PROMPT
    assert "personal-resume" in stored


def test_seed_upgrades_previous_dynamic_relation_entity_prompt(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    previous_prompt = (
        f"{PREVIOUS_FIXED_RELATION_ENTITY_PROMPT} For every supported entity edge, choose the most appropriate relation type "
        "for the meaning and direction stated by the source. Relation types are not limited to a predefined relation list; "
        "use a concise descriptive label rather than a generic fallback when the source supports something more specific. "
        "Whenever a property is added or removed, rebuild the complete entity relationship set from all current non-image "
        "properties so existing relationships and their types may change in light of the complete current graph."
    )
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO system_config(key,value,updated_at) VALUES ('entity_prompt',?,'now')",
            (previous_prompt,),
        )

    seed_defaults(settings)

    with connect(settings.sqlite_path) as db:
        stored = db.execute(
            "SELECT value FROM system_config WHERE key='entity_prompt'",
        ).fetchone()["value"]
    assert stored == DEFAULT_ENTITY_PROMPT
    assert "one plain sentence" in stored


def test_seed_upgrades_the_previous_default_entity_prompt(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    previous_prompt = (
        "Only extract key nouns from the property content, such as human names, product names, "
        "technology stacks, brand names, and company names. Prioritize nouns mentioned many times. "
        "Do not extract generic words, file names, sentences, or summaries. Return an entity identifier "
        "and one brief definition for each noun. Some entities may already exist, so resolve against this "
        "current entity inventory of identifier and definition: {current_entities}"
    )
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO system_config(key,value,updated_at) VALUES ('entity_prompt',?,'now')",
            (previous_prompt,),
        )

    seed_defaults(settings)

    with connect(settings.sqlite_path) as db:
        stored = db.execute(
            "SELECT value FROM system_config WHERE key='entity_prompt'",
        ).fetchone()["value"]
    assert stored == DEFAULT_ENTITY_PROMPT
    assert "meaning is consistent" in stored


def test_seed_upgrades_entity_prompt_that_forced_a_single_connected_graph(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    previous_prompt = (
        "Only extract notional nouns that refer to specific identifiable objects, such as people, products, "
        "technology stacks, brands, companies, organizations, or places. Do not extract generic common nouns, "
        "abstract qualities, actions, file names, sentences, or summaries. When prioritizing words that occur "
        "frequently, count repetitions only when the word has the same meaning in every context; separate or "
        "ignore occurrences that refer to different objects or meanings. The entity identifier may differ slightly "
        "from the original text when a concise canonical or clarified name helps the user understand what the object "
        "is, but do not invent information. Return the entity identifier and one brief definition for each object. "
        "Some entities may already exist, so resolve against this current entity inventory of identifier and "
        "definition: {current_entities}"
    )
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO system_config(key,value,updated_at) VALUES ('entity_prompt',?,'now')",
            (previous_prompt,),
        )

    seed_defaults(settings)

    with connect(settings.sqlite_path) as db:
        stored = db.execute(
            "SELECT value FROM system_config WHERE key='entity_prompt'"
        ).fetchone()["value"]
    assert stored == DEFAULT_ENTITY_PROMPT
    assert "Independent subgraphs" in stored


def test_seed_upgrades_previous_fixed_relation_entity_prompt(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    previous_prompt = (
        "Only extract notional nouns that refer to specific identifiable objects, such as people, products, "
        "technology stacks, brands, companies, organizations, or places. Do not extract generic common nouns, "
        "abstract qualities, actions, file names, sentences, or summaries. When prioritizing words that occur "
        "frequently, count repetitions only when the word has the same meaning in every context; separate or "
        "ignore occurrences that refer to different objects or meanings. The entity identifier may differ slightly "
        "from the original text when a concise canonical or clarified name helps the user understand what the object "
        "is, but do not invent information. Return the entity identifier and one brief definition for each object. "
        "Some entities may already exist, so resolve against this current entity inventory of identifier and "
        "definition: {current_entities} The entity graph may contain multiple independent subgraphs and isolated entities. "
        "Create a relationship only when the source explicitly states or clearly establishes a meaningful relation "
        "between two specific entities. Do not force relationships between unrelated entities. Do not connect entities "
        "merely because they occur in the same property, sentence, topic, or inventory. It is valid to return entities "
        "with no relationships."
    )
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO system_config(key,value,updated_at) VALUES ('entity_prompt',?,'now')",
            (previous_prompt,),
        )

    seed_defaults(settings)

    with connect(settings.sqlite_path) as db:
        stored = db.execute(
            "SELECT value FROM system_config WHERE key='entity_prompt'"
        ).fetchone()["value"]
    assert stored == DEFAULT_ENTITY_PROMPT
    assert "specific relation type" in stored


def test_seed_removes_legacy_property_filename_suggestions(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    catalog_path = settings.projects_dir / "legacy-project" / "jobs" / "property-catalog.json"
    catalog_path.parent.mkdir(parents=True)
    catalog_path.write_text(
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

    seed_defaults(settings)

    assert "filename_suggestion" not in catalog_path.read_text(encoding="utf-8")


def test_mcp_enablement_setting_blocks_manual_open():
    with TestClient(app) as client:
        headers = _headers(client)
        disabled = client.patch("/api/system/config", json={"mcp_enabled": False}, headers=headers)
        assert disabled.status_code == 200
        assert disabled.json()["mcp"]["enabled"] is False
        project = client.post("/api/projects", json={"name": f"MCP Disabled-{uuid4().hex}"}, headers=headers).json()
        blocked = client.post(f"/api/projects/{project['id']}/mcp/open", headers=headers)
        assert blocked.status_code == 503
        client.patch("/api/system/config", json={"mcp_enabled": True}, headers=headers).raise_for_status()


def test_selected_provider_profile_is_resolved_for_runtime_chat_route():
    with TestClient(app) as client:
        headers = _headers(client)
        profile = client.post("/api/system/providers", json={"name": f"Runtime LLM {uuid4().hex}", "provider_type": "llm", "model": "runtime-model", "base_url": "https://provider.test/v1", "secret": "runtime-secret"}, headers=headers)
        profile.raise_for_status()
        client.patch("/api/system/config", json={"dg_agent_route": profile.json()["id"]}, headers=headers).raise_for_status()
        provider = chat_provider(get_settings(), route_key="dg_agent_route")
        assert provider is not None
        assert provider.model == "runtime-model"
        assert provider.base_url == "https://provider.test/v1"
        assert provider.api_key == "runtime-secret"
        cleared = client.patch("/api/system/config", json={"dg_agent_route": None}, headers=headers)
        assert cleared.status_code == 200
        assert cleared.json()["routes"]["dg_agent_route"] is None


def test_provider_profile_validation_reports_readiness_without_returning_secret(monkeypatch):
    import backend.app.api.system as system

    monkeypatch.setattr(system, "probe_provider_profile", lambda *_args: {"ready": True, "provider_type": "embedding", "model": "probe-model", "dimensions": 3})
    with TestClient(app) as client:
        headers = _headers(client)
        profile = client.post("/api/system/providers", json={"name": f"Probe {uuid4().hex}", "provider_type": "embedding", "model": "probe-model", "base_url": "https://provider.test/v1", "secret": "probe-secret"}, headers=headers).json()
        response = client.post(f"/api/system/providers/{profile['id']}/validate", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"ready": True, "provider_type": "embedding", "model": "probe-model", "dimensions": 3}
    assert "probe-secret" not in response.text


def test_provider_profile_can_be_edited_and_removed_without_leaving_route_references():
    with TestClient(app) as client:
        headers = _headers(client)
        profile = client.post("/api/system/providers", json={"name": f"Editable {uuid4().hex}", "provider_type": "llm", "model": "old-model", "base_url": "https://old.test/v1", "secret": "old-secret"}, headers=headers)
        profile.raise_for_status()
        profile_id = profile.json()["id"]
        client.patch("/api/system/config", json={"dg_agent_route": profile_id}, headers=headers).raise_for_status()

        updated = client.patch(f"/api/system/providers/{profile_id}", json={"name": "Edited provider", "model": "new-model", "base_url": "https://new.test/v1", "secret": "new-secret"}, headers=headers)
        assert updated.status_code == 200
        assert updated.json()["model"] == "new-model"
        assert "secret" not in updated.json()

        removed = client.delete(f"/api/system/providers/{profile_id}", headers=headers)
        assert removed.status_code == 200
        assert client.get("/api/system/config", headers=headers).json()["routes"]["dg_agent_route"] is None


def test_disabled_neo4j_reports_neutral_local_mode():
    with TestClient(app) as client:
        result = client.get("/api/system/neo4j/check", headers=_headers(client))
        assert result.status_code == 200
        assert result.json()["mode"] == "local-fallback"
        assert result.json()["configured"] is False
        assert result.json()["message"] == "Local graph storage active"


def test_system_config_exposes_default_retrieval_limits(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    seed_defaults(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            response = client.get("/api/system/config", headers=_headers(client))
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200
    assert response.json()["ai_query_history_compaction_token_threshold"] == 150_000
    assert response.json()["retrieval"] == {
        "ai_query": {
            "property_limit": 15,
            "entity_limit": 15,
            "total_node_limit": 30,
        },
        "search": {
            "property_limit": 30,
            "entity_limit": 30,
        },
    }


def test_system_config_persists_custom_retrieval_limits(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    seed_defaults(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            headers = _headers(client)
            response = client.patch(
                "/api/system/config",
                json={
                    "ai_query_history_compaction_token_threshold": 175_000,
                    "ai_query_property_limit": 9,
                    "ai_query_entity_limit": 11,
                    "ai_query_total_node_limit": 17,
                    "search_property_limit": 41,
                    "search_entity_limit": 43,
                },
                headers=headers,
            )
            reloaded = client.get("/api/system/config", headers=headers)
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200
    assert (
        reloaded.json()["ai_query_history_compaction_token_threshold"]
        == 175_000
    )
    assert reloaded.json()["retrieval"] == {
        "ai_query": {
            "property_limit": 9,
            "entity_limit": 11,
            "total_node_limit": 17,
        },
        "search": {
            "property_limit": 41,
            "entity_limit": 43,
        },
    }


def test_system_config_rejects_non_positive_retrieval_limits(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    seed_defaults(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            response = client.patch(
                "/api/system/config",
                json={"ai_query_total_node_limit": 0},
                headers=_headers(client),
            )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 422


def test_system_config_rejects_non_positive_history_compaction_threshold(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    seed_defaults(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            response = client.patch(
                "/api/system/config",
                json={"ai_query_history_compaction_token_threshold": 0},
                headers=_headers(client),
            )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 422
