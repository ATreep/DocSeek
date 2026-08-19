import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings
from backend.app.db import connect, initialize
from backend.app.main import app
from backend.app.seed import seed_defaults
from backend.app.services.graph_store import GraphRAGBuilder
from backend.app.services.providers import OpenAIChatProvider, ProviderError


def _logged_provider(
    settings: Settings, handler, *, route_key: str = "dg_agent_route"
) -> OpenAIChatProvider:
    return OpenAIChatProvider(
        "audit-model",
        "https://llm.test/v1",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ).enable_invocation_logging(
        settings.sqlite_path,
        route_key=route_key,
        profile_id="profile-1",
    )


def test_complete_llm_invocation_saves_prompt_output_and_timestamps(tmp_path):
    settings = Settings(data_dir=tmp_path)
    initialize(settings.sqlite_path)
    provider = _logged_provider(
        settings,
        lambda _request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Generated definition"}}]},
        ),
    )

    assert provider.complete([{"role": "user", "content": "Define the asset"}]) == "Generated definition"

    with connect(settings.sqlite_path) as db:
        row = db.execute("SELECT * FROM llm_invocation_logs").fetchone()
    assert row is not None
    assert row["status"] == "success"
    assert row["model"] == "audit-model"
    assert row["route_key"] == "dg_agent_route"
    assert row["profile_id"] == "profile-1"
    assert json.loads(row["request_prompt"]) == [
        {"role": "user", "content": "Define the asset"}
    ]
    assert row["response_output"] == "Generated definition"
    assert row["request_time"] <= row["response_time"]
    assert row["duration_ms"] >= 0


def test_streaming_and_failed_llm_invocations_are_both_saved(tmp_path):
    settings = Settings(data_dir=tmp_path)
    initialize(settings.sqlite_path)
    stream_body = (
        'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    streaming = _logged_provider(
        settings, lambda _request: httpx.Response(200, text=stream_body)
    )
    failing = _logged_provider(
        settings, lambda _request: httpx.Response(502, text="upstream unavailable")
    )

    assert list(streaming.stream([{"role": "user", "content": "Say hello"}])) == [
        "Hello",
        " world",
    ]
    try:
        failing.complete([{"role": "user", "content": "Fail"}])
    except ProviderError:
        pass
    else:
        raise AssertionError("expected ProviderError")

    with connect(settings.sqlite_path) as db:
        rows = db.execute(
            "SELECT status,response_output FROM llm_invocation_logs ORDER BY rowid"
        ).fetchall()
    assert [row["status"] for row in rows] == ["success", "error"]
    assert rows[0]["response_output"] == "Hello world"
    assert rows[1]["response_output"] == "upstream unavailable"


def test_entity_generation_responses_rejected_by_validation_are_logged_as_failed(
    tmp_path,
):
    settings = Settings(data_dir=tmp_path)
    initialize(settings.sqlite_path)
    invalid_output = (
        '{"entities":[["invalid entity id","Invalid","Invalid identifier"]]}'
    )
    provider = _logged_provider(
        settings,
        lambda _request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": invalid_output}}]},
        ),
        route_key="entity_agent_route",
    )
    builder = GraphRAGBuilder(
        schema="DocSeekEntity(name,type,definition)",
        prompt="Extract entities",
        llm=provider,
    )

    with pytest.raises(ValueError, match="invalid entity id"):
        builder.build(
            [
                {
                    "project_id": "project-1",
                    "property_id": "property-1",
                    "property_type": "text",
                    "text": "Invalid is mentioned here.",
                }
            ],
        )

    with connect(settings.sqlite_path) as db:
        rows = db.execute(
            "SELECT route_key,status,response_output FROM llm_invocation_logs ORDER BY rowid"
        ).fetchall()
    assert len(rows) == 3
    assert {row["route_key"] for row in rows} == {"entity_agent_route"}
    assert {row["status"] for row in rows} == {"error"}
    assert {row["response_output"] for row in rows} == {invalid_output}


def test_system_api_lists_newest_llm_invocations_for_config_viewers(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    seed_defaults(settings)
    provider = _logged_provider(
        settings,
        lambda _request: httpx.Response(
            200, json={"choices": [{"message": {"content": "Visible output"}}]}
        ),
    )
    provider.complete([{"role": "user", "content": "Visible prompt"}])
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            token = client.post(
                "/api/auth/login", json={"username": "admin", "password": "admin"}
            ).json()["token"]
            response = client.get(
                "/api/system/llm-invocations?limit=1",
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["request_prompt"].find("Visible prompt") >= 0
    assert response.json()[0]["response_output"] == "Visible output"
