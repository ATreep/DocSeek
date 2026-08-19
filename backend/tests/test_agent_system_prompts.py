from pathlib import Path

import pytest

from backend.app.services.agents import (
    DGAgent,
    GAAgent,
    PGBAgent,
    PropertyFilenameAgent,
)
from backend.app.services.graph_store import GraphRAGBuilder
from backend.app.services.display_language import display_language_scope
from backend.app.services.llm import AnswerLLM


class RecordingProvider:
    def __init__(self, response: str):
        self.response = response
        self.messages = []

    def complete(self, messages, **kwargs):
        self.messages.append(messages)
        return self.response


def assert_role_prompt(messages, role_name: str):
    system_message = messages[0]
    assert system_message["role"] == "system"
    article = "an" if role_name[0].casefold() in "aeiou" else "a"
    assert system_message["content"].startswith(
        f"You are {article} {role_name}. Your role is to "
    )
    assert "DocSeek" not in system_message["content"]


def test_every_agent_invocation_uses_a_role_specific_unbranded_system_prompt():
    definition_provider = RecordingProvider(
        '{"definition":"An introduction to Atlas.","property_id":"atlas-guide"}'
    )
    DGAgent(provider=definition_provider).generate(
        "atlas.md", "markdown", "Atlas product documentation."
    )
    assert_role_prompt(
        definition_provider.messages[0], "Definition Generation Agent"
    )

    filename_provider = RecordingProvider(
        '{"suggestions":[{"import_id":"new","filename":"atlas-guide.md"}]}'
    )
    PropertyFilenameAgent(provider=filename_provider).suggest_many(
        {},
        [
            {
                "import_id": "new",
                "original_filename": "README.md",
                "property_type": "markdown",
                "definition": "An introduction to Atlas.",
            }
        ],
    )
    assert_role_prompt(
        filename_provider.messages[0], "Property Filename Generation Agent"
    )
    grouping_provider = RecordingProvider(
        '{"placements":[{"type":"group","name":"Product","content":['
        '{"type":"group","name":"Atlas","content":['
        '{"type":"property","name":"atlas.md","property_id":"target-property"}'
        ']}]}]}'
    )
    GAAgent(provider=grouping_provider).suggest_path(
        "An introduction to Atlas.", filename="atlas.md"
    )
    assert_role_prompt(grouping_provider.messages[0], "Group Arrangement Agent")

    property_graph_provider = RecordingProvider('{"edges":[]}')
    PGBAgent(provider=property_graph_provider).propose(
        [
            {"id": "manual", "filename": "manual.md", "definition": "A manual."},
            {
                "id": "architecture",
                "filename": "architecture.md",
                "definition": "An architecture guide.",
            },
        ]
    )
    assert_role_prompt(
        property_graph_provider.messages[0], "Property Graph Building Agent"
    )

    entity_provider = RecordingProvider('{"entities":[],"edges":[]}')
    GraphRAGBuilder(
        schema="Entity(name,definition)",
        prompt="Extract entities.",
        llm=entity_provider,
    ).build(
        [
            {
                "project_id": "project",
                "property_id": "property",
                "property_type": "text",
                "text": "Atlas uses Neo4j.",
            }
        ]
    )
    assert_role_prompt(entity_provider.messages[0], "Entity Extraction Agent")

    assert_role_prompt(
        AnswerLLM._messages("What is Atlas?", {"properties": [], "entities": []}),
        "AI Query Agent",
    )


def test_definition_generation_agent_generates_the_property_identifier():
    provider = RecordingProvider(
        '{"definition":"An introduction to Atlas installation and usage.",'
        '"property_id":"atlas-product-guide"}'
    )

    result = DGAgent(provider=provider).generate(
        "产品说明.md",
        "markdown",
        "Atlas product installation and usage documentation.",
    )

    assert result.property_id == "atlas-product-guide"
    prompt = provider.messages[0][1]["content"]
    assert "property_id" in prompt
    assert "readable lowercase English" in prompt
    assert "personal-resume" in prompt
    assert "staff-management-system" in prompt


def test_model_invocations_follow_the_display_language():
    instruction = "Output your results in language Chinese."

    with display_language_scope("Chinese"):
        definition_provider = RecordingProvider(
            '{"definition":"Atlas 产品介绍。","property_id":"atlas-guide"}'
        )
        DGAgent(provider=definition_provider).generate(
            "atlas.md", "markdown", "Atlas product documentation."
        )

        filename_provider = RecordingProvider(
            '{"suggestions":[{"import_id":"new","filename":"Atlas-手册.md"}]}'
        )
        PropertyFilenameAgent(provider=filename_provider).suggest_many(
            {},
            [
                {
                    "import_id": "new",
                    "original_filename": "README.md",
                    "property_type": "markdown",
                    "definition": "An introduction to Atlas.",
                }
            ],
        )

        grouping_provider = RecordingProvider(
            '{"placements":[{"type":"group","name":"产品","content":['
            '{"type":"property","name":"atlas.md","property_id":"target-property"}'
            ']}]}'
        )
        GAAgent(provider=grouping_provider).suggest_path(
            "An introduction to Atlas.", filename="atlas.md"
        )

        property_graph_provider = RecordingProvider(
            '{"edges":[{"source":"manual","target":"architecture","type":"说明架构"}]}'
        )
        PGBAgent(provider=property_graph_provider).propose(
            [
                {"id": "manual", "filename": "manual.md", "definition": "A manual."},
                {
                    "id": "architecture",
                    "filename": "architecture.md",
                    "definition": "An architecture guide.",
                },
            ]
        )

        entity_provider = RecordingProvider(
            '{"entities":[["atlas","Atlas","一个产品。",[0]],'
            '["neo4j","Neo4j","一个图数据库。",[0]]],'
            '"edges":[["atlas","neo4j","使用"]]}'
        )
        GraphRAGBuilder(
            schema="Entity(name,definition)",
            prompt="Extract entities.",
            llm=entity_provider,
        ).build(
            [
                {
                    "project_id": "project",
                    "property_id": "property",
                    "property_type": "text",
                    "text": "Atlas uses Neo4j.",
                }
            ]
        )

        ai_messages = AnswerLLM._messages(
            "What is Atlas?", {"properties": [], "entities": []}
        )

    assert instruction in definition_provider.messages[0][-1]["content"]
    assert instruction in filename_provider.messages[0][-1]["content"]
    assert "Keep property_id as 2-5 readable lowercase English ASCII words" in definition_provider.messages[0][-1]["content"]
    assert "Keep import_id unchanged" in filename_provider.messages[0][-1]["content"]
    assert instruction in grouping_provider.messages[0][-1]["content"]
    assert instruction in property_graph_provider.messages[0][-1]["content"]
    assert "Write definitions and names in Chinese" in entity_provider.messages[0][0]["content"]
    assert instruction in ai_messages[-1]["content"]


def test_backend_services_do_not_contain_docseek_branded_system_personas():
    services_directory = Path(__file__).parents[1] / "app" / "services"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in services_directory.glob("*.py")
    )

    assert "You are DocSeek" not in source


def test_filename_generation_allows_unicode_display_filenames():
    provider = RecordingProvider(
        '{"suggestions":[{"import_id":"new","filename":"产品手册.md"}]}'
    )

    agent = PropertyFilenameAgent(provider=provider)
    result = agent.suggest_many(
        {},
        [
            {
                "import_id": "new",
                "original_filename": "产品说明.md",
                "property_type": "markdown",
                "definition": "Atlas 产品的安装与使用手册。",
            }
        ],
    )

    assert result == {"new": "产品手册.md"}
    assert not hasattr(agent, "property_ids")
    assert "Unicode filenames" in provider.messages[0][1]["content"]
    prompt = provider.messages[0][1]["content"]
    assert "property_id" not in prompt
    assert "personal-resume" not in prompt
    assert "staff-management-system" not in prompt


def test_group_arrangement_prompt_only_copies_supplied_property_identifiers():
    provider = RecordingProvider(
        '{"placements":[{"type":"group","name":"Product","content":['
        '{"type":"group","name":"Manuals","content":['
        '{"type":"property","name":"产品手册.md","property_id":"property-1"}'
        ']}]}]}'
    )
    tree = {
        "group_name": "Project",
        "group_path": "",
        "properties": [
            {
                "property_id": "property-1",
                "filename": "产品手册.md",
                "definition": "Atlas 产品的安装与使用手册。",
            }
        ],
        "groups": [],
    }

    proposal = GAAgent(provider=provider).propose_tree(
        tree, "将文件放入产品手册组。"
    )

    assert proposal.directories == {"property-1": "Product/Manuals"}
    system_prompt = provider.messages[0][0]["content"]
    user_prompt = provider.messages[0][1]["content"]
    assert "copy every supplied property_id exactly" in system_prompt
    assert "ASCII property_id" not in system_prompt
    assert "letters, numbers, `-`, or `_`" not in system_prompt
    assert "Copy supplied property_id values exactly" in user_prompt
    assert "readable English word combination" not in user_prompt
    assert "filenames may be Unicode" in user_prompt


def test_group_arrangement_validation_preserves_the_original_llm_response():
    raw_response = (
        '{"placements":[{"type":"group","name":"Product","content":['
        '{"type":"property","name":"产品手册.md","property_id":"产品手册.md"}'
        ']}]}'
    )
    provider = RecordingProvider(raw_response)
    tree = {
        "group_name": "Project",
        "group_path": "",
        "properties": [
            {
                "property_id": "property-1",
                "filename": "产品手册.md",
                "definition": "Atlas 产品的安装与使用手册。",
            }
        ],
        "groups": [],
    }

    with pytest.raises(ValueError) as error:
        GAAgent(provider=provider).propose_tree(tree, "Move the manual.")

    assert getattr(error.value, "llm_response", None) == raw_response


def test_ai_query_system_prompt_explains_graph_tool_workflow():
    prompt = AnswerLLM._messages(
        "Where is the Atlas manual?", {"properties": [], "entities": []}
    )[0]["content"]

    for tool_name in (
        "query_entities",
        "query_properties",
        "get_entity_detail",
        "get_property_detail",
        "get_property_group_tree",
        "read_property_content",
    ):
        assert tool_name in prompt
    assert prompt.index("query_entities") < prompt.index("get_entity_detail")
    assert prompt.index("query_properties") < prompt.index("get_property_detail")
