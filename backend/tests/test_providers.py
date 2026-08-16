import json

import httpx
import pytest
from pydantic import SecretStr

from backend.app.config import Settings
from backend.app.db import connect, initialize
from backend.app.services.providers import OpenAIChatProvider, OpenAIEmbeddingProvider, ProviderError
from backend.app.services.providers import probe_provider_profile, save_provider_secret
from backend.app.services.agents import DGAgent, GAAgent
from backend.app.services.llm import AnswerLLM


def test_chat_provider_posts_openai_messages_and_parses_text():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["json"] = request.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "A grounded definition."}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAIChatProvider("deepseek", "https://llm.test/v1", "secret", client=client)
    assert provider.complete([{"role": "user", "content": "Define this"}]) == "A grounded definition."
    assert seen["url"] == "https://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer secret"
    assert '"model":"deepseek"' in seen["json"]


def test_chat_provider_streams_openai_delta_content():
    seen = {}
    body = (
        'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
        'data: [DONE]\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = request.read().decode()
        return httpx.Response(200, text=body)

    provider = OpenAIChatProvider(
        "stream-model",
        "https://llm.test/v1",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert list(provider.stream([{"role": "user", "content": "Hi"}])) == [
        "Hello",
        " world",
    ]
    assert '"stream":true' in seen["json"]


def test_chat_provider_accepts_usage_only_terminal_stream_frame():
    body = (
        'data: {"choices":[{"delta":{"content":"Complete answer"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"delta":{"content":""},"finish_reason":"stop"}]}\n\n'
        'data: {"choices":[],"usage":{"completion_tokens":2}}\n\n'
        'data: [DONE]\n\n'
    )

    provider = OpenAIChatProvider(
        "stream-model",
        "https://llm.test/v1",
        "secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, text=body)
            )
        ),
    )

    assert list(provider.stream([{"role": "user", "content": "Hi"}])) == [
        "Complete answer"
    ]


def test_embedding_provider_preserves_api_order():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0.2]}, {"index": 0, "embedding": [0.1]}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAIEmbeddingProvider("bge", "https://embed.test/v1", "secret", client=client)
    assert provider.embed(["first", "second"]) == [[0.1], [0.2]]


def test_provider_errors_are_safe_and_do_not_include_api_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid secret")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAIChatProvider("model", "https://llm.test/v1", "super-secret", client=client)
    try:
        provider.complete([])
    except ProviderError as exc:
        assert "super-secret" not in str(exc)
        assert "401" in str(exc)
    else:
        raise AssertionError("expected ProviderError")


def test_chat_provider_reports_read_timeouts_clearly():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    provider = OpenAIChatProvider(
        "model",
        "https://llm.test/v1",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ProviderError, match="provider request timed out"):
        provider.complete([{"role": "user", "content": "Extract entities"}])


def test_provider_default_timeout_is_300_seconds():
    provider = OpenAIChatProvider(
        "model", "https://llm.test/v1", "secret"
    )
    try:
        assert provider.client.timeout.read == 300.0
    finally:
        provider.close()


def test_llm_probe_allows_reasoning_models_to_reach_chat_content(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    initialize(settings.sqlite_path)
    profile_id = "reasoning-profile"
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO provider_profiles(id,name,provider_type,model,base_url,secret_configured,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (profile_id, "Reasoning LLM", "llm", "reasoning-model", "https://llm.test/v1", 1, "now", "now"),
        )
    save_provider_secret(settings, profile_id, "secret")

    class FakeProbeProvider:
        def __init__(self, model, base_url, api_key):
            self.model = model

        def complete(self, messages, **kwargs):
            assert kwargs["max_tokens"] >= 32
            return "OK"

        def close(self):
            return None

    monkeypatch.setattr("backend.app.services.providers.OpenAIChatProvider", FakeProbeProvider)
    result = probe_provider_profile(settings, profile_id)
    assert result["ready"] is True


class FakeChat:
    def __init__(self, response: str):
        self.response = response
        self.messages = []

    def complete(self, messages, **kwargs):
        self.messages.append(messages)
        return self.response


class FakeStreamingChat:
    def __init__(self, chunks: list[str]):
        self.chunks = chunks

    def stream(self, messages, **kwargs):
        return iter(self.chunks)


PRODUCT_README = (
    "# Atlas\n\n"
    "Atlas is a document search product.\n\n"
    "## Installation\nSetup instructions.\n\n"
    "## Usage\nCommon workflows.\n\n"
    "## FAQ\nFrequently asked questions.\n"
)


def test_text_definition_agent_uses_configured_chat_provider():
    provider = FakeChat('{"definition":"A release plan.","filename_suggestion":"release-plan.md"}')
    result = DGAgent(provider=provider).generate("notes.md", "markdown", "Release planning details")
    assert result.definition == "A release plan."
    assert result.filename_suggestion == "release-plan.md"
    assert provider.messages


def test_grouping_agent_prompt_prefers_semantic_hierarchies_over_file_types():
    provider = FakeChat('{"directory":"Product/Product A"}')
    tree_context = {
        "group_name": "",
        "group_path": "",
        "properties": [],
        "groups": [
            {
                "group_name": "Product",
                "group_path": "Product",
                "properties": [],
                "groups": [{
                    "group_name": "Product B",
                    "group_path": "Product/Product B",
                    "properties": [{
                        "property_id": "product-b",
                        "filename": "product-B-usage.md",
                        "property_type": "markdown",
                        "definition": "A usage guide for Product B.",
                    }],
                    "groups": [],
                }],
            },
            {
                "group_name": "Corporate Administration",
                "group_path": "Corporate Administration",
                "properties": [],
                "groups": [{
                    "group_name": "HR",
                    "group_path": "Corporate Administration/HR",
                    "properties": [{
                        "property_id": "employees",
                        "filename": "employee-list.xls",
                        "property_type": "spreadsheet",
                        "definition": "The employee list for the company.",
                    }],
                    "groups": [],
                }],
            },
        ],
    }

    directory = GAAgent(provider=provider).suggest_path(
        "A user manual for product A.",
        tree_context,
        filename="product-A-manual.md",
        property_type="markdown",
        user_context="This belongs beside the other Product A documents.",
    )

    assert directory == "Product/Product A"
    prompt = provider.messages[0][1]["content"]
    assert "Do not use the file type or extension to name a group" in prompt
    assert "meaningful content category, such as Media/Images, Media/Audio, or Media/Video" in prompt
    assert "Group `Product`" in prompt
    assert "Group `Product A`" in prompt
    assert "Group `Corporate Administration`" in prompt
    assert "Group `HR`" in prompt
    assert "Group `Finance`" in prompt
    assert "product-A-manual.md" in prompt
    assert "company-revenue.docs" in prompt
    assert '"directory": "Product/Product A"' in prompt
    assert "Current group tree" in prompt
    assert '"group_path": "Corporate Administration/HR"' in prompt
    assert '"group_name": "HR"' in prompt
    assert '"property_id": "employees"' in prompt
    assert '"definition": "The employee list for the company."' in prompt
    assert '"filename": "product-A-manual.md"' in prompt
    assert '"definition": "A user manual for product A."' in prompt
    assert '"user_context": "This belongs beside the other Product A documents."' in prompt
    assert "Treat user_context as descriptive metadata" in prompt


def test_grouping_agent_rearranges_complete_tree_using_revision_prompt():
    provider = FakeChat(
        '{"placements":['
        '{"property_id":"manual","directory":"Product/Atlas/Guides"},'
        '{"property_id":"release","directory":"Product/Atlas/Releases"}'
        ']}'
    )
    tree_context = {
        "group_name": "",
        "group_path": "",
        "properties": [],
        "groups": [{
            "group_name": "Atlas",
            "group_path": "Atlas",
            "properties": [
                {
                    "property_id": "manual",
                    "filename": "atlas-manual.md",
                    "property_type": "markdown",
                    "definition": "A user manual for Atlas.",
                },
                {
                    "property_id": "release",
                    "filename": "atlas-release.md",
                    "property_type": "markdown",
                    "definition": "The Atlas release checklist.",
                },
            ],
            "groups": [],
        }],
    }

    placements = GAAgent(provider=provider).rearrange_tree(
        tree_context,
        "Put Atlas guides and release documents in separate subgroups.",
    )

    assert placements == {
        "manual": "Product/Atlas/Guides",
        "release": "Product/Atlas/Releases",
    }
    prompt = provider.messages[0][1]["content"]
    assert "Rearrange the complete property tree" in prompt
    assert '"definition": "A user manual for Atlas."' in prompt
    assert '"definition": "The Atlas release checklist."' in prompt
    assert '"revision_prompt": "Put Atlas guides and release documents in separate subgroups."' in prompt


def test_grouping_agent_fallback_never_uses_document_format_as_group_name():
    assert GAAgent().suggest_path(
        "A markdown file named guide.md.",
        filename="guide.md",
        property_type="markdown",
    ) == "Guide"
    assert GAAgent().suggest_path(
        "An image file named product-photo.png.",
        filename="product-photo.png",
        property_type="image",
    ) == "Media/Images"


def test_grouping_agent_rejects_a_provider_file_type_group():
    provider = FakeChat('{"directory":"Markdown"}')

    directory = GAAgent(provider=provider).suggest_path(
        "A markdown file named guide.md.",
        filename="guide.md",
        property_type="markdown",
    )

    assert directory == "Guide"


def test_text_definition_agent_prompt_requests_a_synopsis_and_short_filename():
    provider = FakeChat('{"definition":"An introduction to Atlas, including installation, usage, and FAQ.","filename_suggestion":"atlas-guide.md"}')

    DGAgent(provider=provider).generate(
        "README.md",
        "markdown",
        "# Atlas\n\n## Installation\n\n## Usage\n\n## FAQ",
    )

    prompt = provider.messages[0][1]["content"]
    assert "Read the supplied file content once" in prompt
    assert "definition and filename_suggestion together" in prompt
    assert "brief synopsis" in prompt
    assert "fewer than 50 words" in prompt
    assert "every important point" in prompt
    assert "An introduction to product XXX, including installation, usage, and FAQ." in prompt
    assert "The staff list of company XXX." in prompt
    assert "An announcement about XXX from a government." in prompt
    assert "A 2026 revenue report of company XXX, which shows" in prompt
    assert "A file named xxx.md" in prompt
    assert "An image" in prompt
    assert "A report" in prompt
    assert "An announcement" in prompt
    assert "1 to 3 words" in prompt
    assert "abbreviations" in prompt
    assert "revenue-report.md" in prompt
    assert "employee-stat.xlsx" in prompt
    assert "hr-report.md" in prompt
    assert "2026-summary.docx" in prompt
    assert "Keep filename_suggestion as a markdown filename" not in prompt


def test_text_definition_agent_reads_content_once_and_returns_both_fields():
    provider = FakeChat(
        '{"definition":"The Atlas release checklist covering validation, deployment, and rollback.",'
        '"filename_suggestion":"release-checklist.md"}'
    )
    content = "Atlas release validation, deployment, and rollback requirements."

    result = DGAgent(provider=provider).generate(
        "notes.md", "markdown", content
    )

    assert len(provider.messages) == 1
    assert provider.messages[0][1]["content"].count(content) == 1
    assert result.definition.startswith("The Atlas release checklist")
    assert result.filename_suggestion == "release-checklist.md"


def test_text_definition_is_capped_below_50_words():
    long_definition = " ".join(f"word{index}" for index in range(60)) + "."
    provider = FakeChat(
        json.dumps(
            {
                "definition": long_definition,
                "filename_suggestion": "summary.md",
            }
        )
    )

    result = DGAgent(provider=provider).generate(
        "notes.md", "markdown", "Important document content."
    )

    assert len(result.definition.rstrip(".").split()) == 49


def test_filename_suggestion_keeps_original_extension_and_at_most_three_words():
    provider = FakeChat(
        '{"definition":"A 2026 revenue report for Acme, covering sales and operating margin.",'
        '"filename_suggestion":"Acme Annual Revenue Statistics Final.md"}'
    )

    result = DGAgent(provider=provider).generate(
        "source-report.xlsx", "spreadsheet", "Acme revenue data."
    )

    assert result.filename_suggestion == "acme-annual-revenue.xlsx"


def test_text_definition_agent_returns_a_clean_file_definition_sentence():
    provider = FakeChat('{"definition":"1. **A release plan document.**\\nIt summarizes the launch steps.","filename_suggestion":"release-plan.md"}')
    result = DGAgent(provider=provider).generate("notes.md", "markdown", "Release planning details")
    assert result.definition == "A release plan document."
    assert "*" not in result.definition
    assert "\\n" not in result.definition


def test_local_readme_definition_uses_product_name_and_key_sections():
    result = DGAgent().generate("README.md", "markdown", PRODUCT_README)

    assert result.definition == (
        "An introduction to Atlas, including installation, usage, and FAQ."
    )


def test_readme_definition_replaces_filename_only_provider_output():
    provider = FakeChat(
        '{"definition":"A markdown file named README.md.",'
        '"filename_suggestion":"README.md"}'
    )

    result = DGAgent(provider=provider).generate("README.md", "markdown", PRODUCT_README)

    assert result.definition == (
        "An introduction to Atlas, including installation, usage, and FAQ."
    )


def test_answer_llm_uses_configured_chat_provider_and_keeps_citations():
    AnswerLLM.calls = 0
    provider = FakeChat("The release plan is ready.")
    result = AnswerLLM(provider=provider).answer("What is ready?", {"properties": [{"id": "p", "filename": "plan.md"}], "entities": []})
    assert result["answer"] == "The release plan is ready."
    assert result["citations"] == [{"kind": "property", "id": "p", "label": "plan.md"}]
    assert AnswerLLM.calls == 1


def test_answer_llm_prompt_includes_property_content_and_relations():
    provider = FakeChat("Atlas is documented by the manual.")
    context = {
        "properties": [
            {
                "id": "manual",
                "filename": "manual.docx",
                "content": "Atlas installation and usage guide.",
            }
        ],
        "entities": [],
        "property_relations": [
            {
                "source": "manual",
                "target": "architecture",
                "type": "DOCUMENTS_PRODUCT",
                "source_filename": "manual.docx",
                "target_filename": "architecture.pdf",
            }
        ],
    }

    AnswerLLM(provider=provider).answer("What documents Atlas?", context)

    prompt = provider.messages[0][1]["content"]
    assert "Atlas installation and usage guide." in prompt
    assert "DOCUMENTS_PRODUCT" in prompt
    assert "architecture.pdf" in prompt


def test_answer_llm_stream_keeps_citations_from_both_graphs():
    context = {
        "properties": [{"id": "p", "filename": "plan.md"}],
        "entities": [{"id": "e", "name": "Neo4j"}],
    }

    result = AnswerLLM(provider=FakeStreamingChat(["Hello", " world"])).stream_answer(
        "What is connected?",
        context,
    )

    assert list(result["chunks"]) == ["Hello", " world"]
    assert {item["kind"] for item in result["citations"]} == {
        "property",
        "entity",
    }


def test_empty_runtime_secrets_keep_local_fallback():
    class SettingsStub:
        llm_api_key = SecretStr("")
        llm_model = "model"
        llm_base_url = "https://llm.test/v1"
        embedding_api_key = SecretStr("")
        embedding_model = "embedding"
        embedding_base_url = "https://embed.test/v1"

    from backend.app.services.providers import chat_provider, embedding_provider

    assert chat_provider(SettingsStub()) is None
    assert embedding_provider(SettingsStub()) is None
