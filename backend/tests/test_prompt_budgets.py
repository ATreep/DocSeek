from backend.app.services import agents, graph_store, llm, system_prompts


class RecordingProvider:
    def __init__(self, response: str):
        self.response = response
        self.messages = []

    def complete(self, messages, **kwargs):
        self.messages.append(messages)
        return self.response


def test_active_system_prompts_are_compact():
    prompts = {
        name: value
        for name, value in vars(system_prompts).items()
        if name.endswith("_SYSTEM_PROMPT")
    }

    assert prompts
    assert {name: len(value) for name, value in prompts.items()} == {
        name: len(value) for name, value in prompts.items() if len(value) <= 500
    }


def test_definition_generation_instructions_are_compact():
    provider = RecordingProvider(
        '{"definition":"An introduction to Atlas.",'
        '"property_id":"atlas-documentation"}'
    )
    agents.DGAgent(provider=provider).generate(
        "atlas.md", "markdown", "Atlas documentation."
    )

    prompt = provider.messages[0][1]["content"]
    assert len(prompt) <= 900


def test_active_agent_instruction_prompts_are_compact():
    budgets = {
        "filename": (agents.FILENAME_GENERATION_PROMPT, 900),
        "single grouping": (agents.GROUPING_PROMPT, 1_700),
        "re-grouping": (agents.TREE_REARRANGEMENT_PROMPT, 2_300),
        "automatic grouping": (agents.AUTOMATIC_TREE_ORGANIZATION_PROMPT, 1_500),
        "property graph": (agents.PROPERTY_GRAPH_PROMPT, 1_000),
        "entity extraction": (graph_store.DEFAULT_ENTITY_PROMPT, 2_600),
    }

    oversized = {
        name: {"length": len(prompt), "budget": maximum}
        for name, (prompt, maximum) in budgets.items()
        if len(prompt) > maximum
    }
    assert oversized == {}


def test_entity_extraction_call_does_not_repeat_long_instructions():
    provider = RecordingProvider('{"entities":[],"edges":[]}')
    graph_store.GraphRAGBuilder(
        schema="Entity(name,definition)",
        prompt=graph_store.DEFAULT_ENTITY_PROMPT,
        llm=provider,
    ).build(
        [
            {
                "project_id": "project",
                "property_id": "property-1",
                "property_type": "text",
                "text": "Atlas uses Neo4j.",
            }
        ]
    )

    assert len(provider.messages[0][1]["content"]) <= 3_200


def test_ai_query_tool_instructions_are_compact():
    tools = [llm.READ_PROPERTY_CONTENT_TOOL, *llm.PROJECT_GRAPH_TOOLS]
    instruction_text = []
    for tool in tools:
        function = tool["function"]
        instruction_text.append(function["description"])
        for parameter in function["parameters"]["properties"].values():
            instruction_text.append(parameter.get("description", ""))

    assert sum(map(len, instruction_text)) <= 700
