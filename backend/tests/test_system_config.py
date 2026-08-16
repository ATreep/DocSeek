import json
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.config import Settings, get_settings
from backend.app.db import connect, initialize
from backend.app.seed import seed_defaults
from backend.app.services.graph_store import DEFAULT_ENTITY_PROMPT, ENTITY_DEFINITION_GUIDANCE, PREVIOUS_DYNAMIC_RELATION_ENTITY_PROMPT, PREVIOUS_FIXED_RELATION_ENTITY_PROMPT
from backend.app.services.providers import chat_provider


def _headers(client: TestClient) -> dict[str, str]:
    token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


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
        assert "same meaning in every context" in prompt
        assert "identifier may differ slightly" in prompt
        assert "notional nouns" in prompt
        assert "specific identifiable objects" in prompt
        assert "multiple independent subgraphs" in prompt
        assert "Do not force relationships" in prompt
        assert "isolated entities" in prompt
        assert "choose the most appropriate relation type" in prompt
        assert "not limited to a predefined relation list" in prompt
        assert "rebuild the complete entity relationship set" in prompt
        assert "single brief plain-language sentence" in prompt
        assert "25 words or fewer" in prompt
        assert "understand what the entity is at a glance" in prompt
        assert "Do not copy, quote, or lightly rephrase" in prompt
        assert "code snippets" in prompt
        assert "Do not try to extract every noun" in prompt
        assert "A small result is preferable" in prompt
        assert "coding, network, PC, or user" in prompt
        assert "function words, filler words, structural labels" in prompt
        assert "people, companies, organizations, products, brands, or places" in prompt
        assert "professional concepts, standards, laws, or regulations" in prompt
        assert "clearly described in one short sentence" in prompt


def test_seed_upgrades_previous_concise_definition_entity_prompt(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    previous_prompt = f"{PREVIOUS_DYNAMIC_RELATION_ENTITY_PROMPT} {ENTITY_DEFINITION_GUIDANCE}"
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
    assert "Do not try to extract every noun" in stored


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
    assert "single brief plain-language sentence" in stored


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
    assert "same meaning in every context" in stored


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
    assert "multiple independent subgraphs" in stored


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
    assert "not limited to a predefined relation list" in stored


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
