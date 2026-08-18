import json

from backend.app.services.agents import PGBAgent, validate_edge_proposals
from backend.app.services.graph_store import GraphRAGBuilder


def test_pgb_agent_can_only_propose_edges_between_existing_property_nodes():
    inventory = [{"id": "p1", "filename": "one.md", "definition": "One"}, {"id": "p2", "filename": "two.md", "definition": "Two"}]
    proposals = [{"source": "p1", "target": "p2", "type": "documents implementation"}]
    assert all(edge["source"] in {"p1", "p2"} and edge["target"] in {"p1", "p2"} for edge in proposals)
    assert validate_edge_proposals(inventory, proposals) == [
        {"source": "p1", "target": "p2", "type": "DOCUMENTS_IMPLEMENTATION"}
    ]
    try:
        validate_edge_proposals(inventory, [{"source": "p1", "target": "missing", "type": "RELATED"}])
    except ValueError as error:
        assert "endpoint" in str(error)
    else:
        raise AssertionError("invalid endpoint must be rejected")


def test_graph_relation_types_preserve_chinese_labels():
    inventory = [
        {"id": "manual", "filename": "manual.md", "definition": "One"},
        {"id": "architecture", "filename": "architecture.md", "definition": "Two"},
    ]

    assert validate_edge_proposals(
        inventory,
        [{"source": "manual", "target": "architecture", "type": "说明 架构"}],
    ) == [
        {"source": "manual", "target": "architecture", "type": "说明_架构"}
    ]


class FakePropertyGraphChat:
    def __init__(self, response: str):
        self.response = response
        self.messages = []

    def complete(self, messages, **kwargs):
        self.messages.append(messages)
        return self.response


def test_pgb_agent_prompt_allows_independent_subgraphs_without_forced_edges():
    provider = FakePropertyGraphChat(
        '{"edges":[{"source":"manual","target":"architecture","type":"DESCRIBES_ARCHITECTURE"}]}'
    )
    inventory = [
        {"id": "manual", "filename": "product-A-manual.md", "definition": "A user manual for product A."},
        {"id": "architecture", "filename": "product-A-arch.md", "definition": "The architecture of product A."},
        {"id": "employees", "filename": "employee-list.xls", "definition": "An employee list."},
        {"id": "revenue", "filename": "company-revenue.docs", "definition": "A company revenue report."},
    ]

    proposals = PGBAgent(provider=provider).propose(inventory)

    assert proposals == [
        {"source": "manual", "target": "architecture", "type": "DESCRIBES_ARCHITECTURE"}
    ]
    prompt = provider.messages[0][1]["content"]
    assert "Independent subgraphs and isolated nodes are valid" in prompt
    assert "never connect nodes merely by project" in prompt
    assert "Return no edge when evidence is unclear" in prompt
    assert "types are open-ended" in prompt
    assert "precise directed type" in prompt
    assert "product-A-manual.md" in prompt
    assert "company-revenue.docs" in prompt


def test_pgb_agent_prompt_excludes_content_and_embedding_payloads():
    provider = FakePropertyGraphChat('{"edges":[]}')
    inventory = [
        {
            "id": "manual",
            "project_id": "project",
            "filename": "manual.md",
            "property_type": "markdown",
            "definition": "A user manual for Atlas.",
            "directory": "Product/Atlas",
            "relative_path": "properties/Product/Atlas/manual.md",
            "content": "PRIVATE FULL DOCUMENT CONTENT",
            "embedding": [0.1] * 1024,
        },
        {
            "id": "architecture",
            "filename": "architecture.md",
            "property_type": "markdown",
            "definition": "The Atlas architecture.",
            "content": "ANOTHER FULL DOCUMENT",
            "embedding": [0.2] * 1024,
        },
    ]

    PGBAgent(provider=provider).propose(inventory)

    prompt = provider.messages[0][1]["content"]
    assert "manual.md" in prompt
    assert "A user manual for Atlas." in prompt
    assert "Product/Atlas" in prompt
    assert "PRIVATE FULL DOCUMENT CONTENT" not in prompt
    assert "ANOTHER FULL DOCUMENT" not in prompt
    assert '"embedding"' not in prompt
    assert len(prompt) < 3000


def test_pgb_agent_local_fallback_keeps_unrelated_properties_disconnected():
    inventory = [
        {"id": "product", "filename": "product-manual.md", "definition": "A product manual."},
        {"id": "employees", "filename": "employee-list.xls", "definition": "An employee list."},
        {"id": "revenue", "filename": "revenue.docs", "definition": "A revenue report."},
    ]

    assert PGBAgent().propose(inventory) == []


def test_pgb_agent_accepts_json_wrapped_in_provider_quote_characters():
    provider = FakePropertyGraphChat(
        "'{\"edges\":[{\"source\":\"manual\",\"target\":\"architecture\",\"type\":\"EXPLAINS\"}]}'"
    )
    inventory = [
        {"id": "manual", "filename": "manual.md", "definition": "A manual."},
        {"id": "architecture", "filename": "architecture.md", "definition": "An architecture specification."},
    ]

    assert PGBAgent(provider=provider).propose(inventory) == [
        {"source": "manual", "target": "architecture", "type": "EXPLAINS"}
    ]


def test_graphrag_builder_extracts_entities_from_image_descriptions():
    builder = GraphRAGBuilder(schema="DocSeekEntity(name,type)", prompt="Extract entities")
    result = builder.build([
        {"project_id": "p", "property_id": "text-1", "property_type": "text", "text": "Neo4j powers DocSeek."},
        {"project_id": "p", "property_id": "image-1", "property_type": "image", "text": "An architecture diagram shows Atlas connected to Neo4j."},
    ])
    assert builder.last_documents == ["text-1", "image-1"]
    assert {source for entity in result[0] for source in entity["source_property_ids"]} == {"text-1", "image-1"}
    assert builder.schema == "DocSeekEntity(name,type)"
    assert builder.prompt == "Extract entities"


def test_graphrag_builder_stores_entity_definition_without_duplicate_description():
    builder = GraphRAGBuilder(schema="DocSeekEntity(name,type)", prompt="Extract entities")
    entities, _ = builder.build([
        {"project_id": "p", "property_id": "text-1", "property_type": "text", "text": "Atlas service owns document ingestion. Beacon reviews the release."},
    ])

    atlas = next(entity for entity in entities if entity["id"] == "atlas")
    assert atlas["definition"] == "Atlas service owns document ingestion."
    assert "description" not in atlas
    assert atlas["source_contexts"] == [{"property_id": "text-1", "text": "Atlas service owns document ingestion."}]


def test_graphrag_builder_generates_typed_relationships_instead_of_co_occurs():
    builder = GraphRAGBuilder(schema="DocSeekEntity(name,type,definition)", prompt="Extract entities")
    _, edges = builder.build([
        {"project_id": "p", "property_id": "text-1", "property_type": "text", "text": "Atlas uses Neo4j. Beacon owns Atlas."},
    ])
    assert {edge["type"] for edge in edges} == {"USES", "OWNS"}


def test_graphrag_builder_does_not_connect_entities_without_a_stated_relation():
    builder = GraphRAGBuilder(
        schema="DocSeekEntity(name,type,definition)", prompt="Extract entities"
    )

    entities, edges = builder.build(
        [
            {
                "project_id": "p",
                "property_id": "text-1",
                "property_type": "text",
                "text": "Atlas and Beacon appear in this overview.",
            }
        ]
    )

    assert {entity["id"] for entity in entities} >= {"atlas", "beacon"}
    assert edges == []


def test_graphrag_builder_does_not_attach_an_unrelated_third_entity():
    builder = GraphRAGBuilder(
        schema="DocSeekEntity(name,type,definition)", prompt="Extract entities"
    )

    _, edges = builder.build(
        [
            {
                "project_id": "p",
                "property_id": "text-1",
                "property_type": "text",
                "text": "Atlas uses Neo4j while Beacon appears in the overview.",
            }
        ]
    )

    assert edges == [{"source": "atlas", "target": "neo4j", "type": "USES"}]


class FakeEntityExtractionChat:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.messages = []
        self.kwargs = []

    def complete(self, messages, **kwargs):
        self.messages.append(messages)
        self.kwargs.append(kwargs)
        return self.responses.pop(0)


def test_entity_extraction_uses_bounded_text_but_context_comes_from_original_text():
    provider = FakeEntityExtractionChat(
        [
            '{"entities":[["atlas","Atlas","A deployment product.",[0]]],"edges":[]}'
        ]
    )
    builder = GraphRAGBuilder(
        schema="DocSeekEntity(name,type,definition)",
        prompt="Extract entities",
        llm=provider,
    )

    entities, _ = builder.build(
        [
            {
                "project_id": "p",
                "property_id": "p1",
                "property_type": "text",
                "text": "Atlas uses Neo4j.",
                "original_text": (
                    "Private preface. Atlas uses Neo4j for graph retrieval "
                    "and deployment."
                ),
            }
        ]
    )

    prompt = provider.messages[0][1]["content"]
    assert "Atlas uses Neo4j." in prompt
    assert "Private preface" not in prompt
    assert entities[0]["source_contexts"] == [
        {
            "property_id": "p1",
            "text": "Atlas uses Neo4j for graph retrieval and deployment.",
        }
    ]


def test_long_entity_content_is_extracted_in_overlapping_twelve_thousand_character_calls():
    provider = FakeEntityExtractionChat([
        '{"entities":[["alpha","Alpha","The first entity.",[0]]],"edges":[]}',
        '{"entities":[["beta","Beta","The second entity.",[0]]],"edges":[["beta","alpha","FOLLOWS"]]}',
        '{"entities":[["gamma","Gamma","The third entity.",[0]]],"edges":[["gamma","beta","FOLLOWS"]]}',
    ])
    builder = GraphRAGBuilder(
        schema="DocSeekEntity(name,type,definition)",
        prompt="Extract entities",
        llm=provider,
    )
    content = "A" * 11_500 + "B" * 500 + "C" * 11_500 + "D" * 500

    entities, edges = builder.build([{
        "project_id": "p",
        "property_id": "long-property",
        "property_type": "text",
        "text": content,
    }], incremental=True)

    payloads = [
        json.loads(
            messages[1]["content"]
            .split("Current property documents:\n", 1)[1]
            .split("\n\nOutput your results in language ", 1)[0]
        )
        for messages in provider.messages
    ]
    chunks = [payload[0]["text"] for payload in payloads]
    assert [len(chunk) for chunk in chunks] == [12_000, 12_000, 1_000]
    assert chunks[0] == content[0:12_000]
    assert chunks[1] == content[11_500:23_500]
    assert chunks[2] == content[23_000:24_000]
    assert [entity["id"] for entity in entities] == ["alpha", "beta", "gamma"]
    assert edges == [
        {"source": "beta", "target": "alpha", "type": "FOLLOWS"},
        {"source": "gamma", "target": "beta", "type": "FOLLOWS"},
    ]


def test_entity_extraction_retries_invalid_edges_before_returning_a_graph():
    provider = FakeEntityExtractionChat(
        [
            '{"entities":[["atlas","Atlas","A product.",[0]]],"edges":[["atlas","missing","USES"]]}',
            '{"entities":[["atlas","Atlas","A product.",[0]],["neo4j","Neo4j","A graph database.",[0]]],"edges":[["atlas","neo4j","USES"]]}',
        ]
    )
    builder = GraphRAGBuilder(
        schema="DocSeekEntity(name,type,definition)",
        prompt="Extract entities",
        llm=provider,
    )

    entities, edges = builder.build(
        [{"project_id": "p", "property_id": "p1", "property_type": "text", "text": "Atlas uses Neo4j."}]
    )

    assert len(provider.messages) == 2
    assert {entity["id"] for entity in entities} == {"atlas", "neo4j"}
    assert edges == [{"source": "atlas", "target": "neo4j", "type": "USES"}]


def test_entity_extraction_requires_ascii_ids_and_preserves_unicode_names():
    provider = FakeEntityExtractionChat(
        [
            '{"entities":[["北京大学","北京大学","A research university in Beijing.",[0]]],"edges":[]}',
            '{"entities":[["北京大学","北京大学","A research university in Beijing.",[0]]],"edges":[]}',
            '{"entities":[["peking-university","北京大学","A research university in Beijing.",[0]]],"edges":[]}',
        ]
    )
    builder = GraphRAGBuilder(
        schema="DocSeekEntity(name,type,definition)",
        prompt="Extract entities",
        llm=provider,
    )

    entities, edges = builder.build(
        [
            {
                "project_id": "p",
                "property_id": "property-1",
                "property_type": "text",
                "text": "北京大学位于北京。",
            }
        ]
    )

    assert len(provider.messages) == 3
    assert entities[0]["id"] == "peking-university"
    assert entities[0]["name"] == "北京大学"
    assert edges == []
    assert "ASCII IDs" in provider.messages[0][0]["content"]
    prompt = provider.messages[0][1]["content"]
    assert "lowercase English ASCII id" in prompt
    assert "letters/numbers joined by `-` or `_`" in prompt
    assert "original Unicode spelling" in prompt


def test_entity_extraction_llm_can_choose_and_change_relation_types():
    provider = FakeEntityExtractionChat(
        [
            '{"entities":[{"id":"atlas","name":"Atlas","definition":"A product.","source_property_ids":["p1"]},{"id":"neo4j","name":"Neo4j","definition":"A graph database.","source_property_ids":["p1"]}],"edges":[{"source":"atlas","target":"neo4j","type":"USES"}]}',
            '{"entities":[{"id":"atlas","name":"Atlas","definition":"A product.","source_property_ids":["p1","p2"]},{"id":"neo4j","name":"Neo4j","definition":"A graph database.","source_property_ids":["p1"]},{"id":"beacon","name":"Beacon","definition":"A service.","source_property_ids":["p2"]}],"edges":[{"source":"atlas","target":"neo4j","type":"STORES_KNOWLEDGE_IN"},{"source":"beacon","target":"atlas","type":"REVIEWS"}]}',
        ]
    )
    builder = GraphRAGBuilder(
        schema="DocSeekEntity(name,type,definition)",
        prompt="Extract entities",
        llm=provider,
    )

    _, first_edges = builder.build(
        [{"project_id": "p", "property_id": "p1", "property_type": "text", "text": "Atlas uses Neo4j."}]
    )
    _, rebuilt_edges = builder.build(
        [
            {"project_id": "p", "property_id": "p1", "property_type": "text", "text": "Atlas uses Neo4j."},
            {"project_id": "p", "property_id": "p2", "property_type": "text", "text": "Beacon reviews Atlas, which stores knowledge in Neo4j."},
        ]
    )

    assert first_edges == [{"source": "atlas", "target": "neo4j", "type": "USES"}]
    assert rebuilt_edges == [
        {"source": "atlas", "target": "neo4j", "type": "STORES_KNOWLEDGE_IN"},
        {"source": "beacon", "target": "atlas", "type": "REVIEWS"},
    ]
    prompt = provider.messages[0][1]["content"]
    assert "evidence-backed directed relations with a specific type" in prompt
    assert "Full call: rebuild entities and relations" in prompt
    assert "one plain sentence" in prompt
    assert "under 25 words" in prompt
    assert "Do not copy source text, code, logs, or Markdown" in prompt
    assert "Do not extract every noun" in prompt
    assert "generic concepts (user, code, network)" in prompt
    assert "headings, filler, numbers, hashes" in prompt
    assert "laws, standards, and rare professional concepts" in prompt
    assert "meaningful enough for a short definition" in prompt


def test_entity_extraction_accepts_json_wrapped_in_provider_quote_characters():
    provider = FakeEntityExtractionChat(
        [
            "'{\"entities\":[{\"id\":\"atlas\",\"name\":\"Atlas\",\"definition\":\"A product.\",\"source_property_ids\":[\"p1\"]}],\"edges\":[]}'"
        ]
    )
    builder = GraphRAGBuilder(
        schema="DocSeekEntity(name,type,definition)",
        prompt="Extract entities",
        llm=provider,
    )

    entities, edges = builder.build(
        [
            {
                "project_id": "p",
                "property_id": "p1",
                "property_type": "text",
                "text": "Atlas is a product.",
            }
        ]
    )

    assert [entity["id"] for entity in entities] == ["atlas"]
    assert edges == []


def test_entity_extraction_uses_compact_bounded_response_contract():
    provider = FakeEntityExtractionChat(
        [
            '{"entities":[["atlas","Atlas","A deployment product.",[0,1]],["neo4j","Neo4j","A graph database.",[0]]],"edges":[["atlas","neo4j","STORES_IN"]]}'
        ]
    )
    builder = GraphRAGBuilder(
        schema="DocSeekEntity(name,type,definition)",
        prompt="Extract entities",
        llm=provider,
    )

    entities, edges = builder.build(
        [
            {
                "project_id": "p",
                "property_id": "property-with-a-long-stable-id-1",
                "property_type": "text",
                "text": "Atlas stores data in Neo4j.",
            },
            {
                "project_id": "p",
                "property_id": "property-with-a-long-stable-id-2",
                "property_type": "text",
                "text": "Atlas is deployed by the platform team.",
            },
        ]
    )

    atlas = next(entity for entity in entities if entity["id"] == "atlas")
    assert atlas["source_property_ids"] == [
        "property-with-a-long-stable-id-1",
        "property-with-a-long-stable-id-2",
    ]
    assert edges == [{"source": "atlas", "target": "neo4j", "type": "STORES_IN"}]
    assert provider.kwargs[0]["max_tokens"] == 4096
    prompt = provider.messages[0][1]["content"]
    assert '"i":0' in prompt
    assert '"i":1' in prompt
    assert '"property_id"' not in prompt
    assert '["id","name","definition",[document_i]]' in prompt


def test_graphrag_builder_receives_current_entity_inventory():
    builder = GraphRAGBuilder(schema="DocSeekEntity(name,type,definition)", prompt="Extract entities")
    current = [{"id": "neo4j", "definition": "A graph database."}]
    builder.build([
        {"project_id": "p", "property_id": "text-1", "property_type": "text", "text": "Atlas uses Neo4j."},
    ], current_entities=current)
    assert builder.last_entity_inventory == current


def test_incremental_entity_extraction_uses_new_documents_and_existing_inventory():
    provider = FakeEntityExtractionChat(
        [
            '{"entities":[["beacon","Beacon","A deployment service.",[0]]],'
            '"edges":[["beacon","atlas","DEPLOYS_TO"]]}'
        ]
    )
    builder = GraphRAGBuilder(
        schema="DocSeekEntity(name,type,definition)",
        prompt="Extract entities and rebuild the complete graph.",
        llm=provider,
    )

    entities, edges = builder.build(
        [
            {
                "project_id": "p",
                "property_id": "new-property",
                "property_type": "text",
                "text": "Beacon deploys to Atlas.",
            }
        ],
        current_entities=[
            {
                "id": "atlas",
                "name": "Atlas",
                "definition": "A release management product.",
            }
        ],
        incremental=True,
    )

    assert [entity["id"] for entity in entities] == ["beacon"]
    assert edges == [
        {"source": "beacon", "target": "atlas", "type": "DEPLOYS_TO"}
    ]
    assert builder.last_documents == ["new-property"]
    assert builder.last_entity_inventory == [
        {
            "id": "atlas",
            "name": "Atlas",
            "definition": "A release management product.",
        }
    ]
    prompt = provider.messages[0][1]["content"]
    assert "Incremental call: inspect only supplied new text" in prompt
    assert "inventory is reference data" in prompt
    assert '"id":"atlas"' in prompt
    assert '"name":"Atlas"' in prompt
    assert '"definition":"A release management product."' in prompt
    assert "return only mentioned entities" in prompt


def test_graphrag_builder_uses_shared_embedder_for_entities():
    class FakeEmbedder:
        def embed(self, texts):
            return [[float(index + 1)] for index, _ in enumerate(texts)]

    builder = GraphRAGBuilder(schema="DocSeekEntity(name,type)", prompt="Extract entities")
    entities, _ = builder.build([{"project_id": "p", "property_id": "text-1", "property_type": "text", "text": "Neo4j powers DocSeek."}], embedder=FakeEmbedder())
    assert [item["embedding"] for item in entities] == [[1.0], [2.0]]


def test_entity_embedding_includes_name_definition_and_source_context():
    embedded_texts = []

    class FakeEmbedder:
        def embed(self, texts):
            embedded_texts.extend(texts)
            return [[1.0] for _ in texts]

    provider = FakeEntityExtractionChat(
        [
            '{"entities":[{"id":"atlas","name":"Atlas","definition":"A deployment product.","source_property_ids":["p1"]}],"edges":[]}'
        ]
    )
    builder = GraphRAGBuilder(
        schema="DocSeekEntity(name,type,definition)",
        prompt="Extract entities",
        llm=provider,
    )

    entities, _ = builder.build(
        [
            {
                "project_id": "p",
                "property_id": "p1",
                "property_type": "text",
                "text": "Atlas manages release deployments.",
            }
        ],
        embedder=FakeEmbedder(),
    )

    assert entities[0]["source_contexts"] == [
        {"property_id": "p1", "text": "Atlas manages release deployments."}
    ]
    assert embedded_texts == [
        "Atlas\nA deployment product.\nAtlas manages release deployments."
    ]


def test_entity_llm_uses_bounded_text_but_context_comes_from_original_text():
    provider = FakeEntityExtractionChat(
        [
            '{"entities":[{"id":"atlas","name":"Atlas","definition":"A deployment product.","source_property_ids":["p1"]}],"edges":[]}'
        ]
    )
    builder = GraphRAGBuilder(
        schema="DocSeekEntity(name,type,definition)",
        prompt="Extract entities",
        llm=provider,
    )

    entities, _ = builder.build(
        [
            {
                "project_id": "p",
                "property_id": "p1",
                "property_type": "text",
                "text": "Atlas uses Neo4j.",
                "original_text": (
                    "Private preface that should not be sent to the provider. "
                    "Atlas uses Neo4j for graph retrieval and deployment."
                ),
            }
        ]
    )

    prompt = provider.messages[0][1]["content"]
    assert "Atlas uses Neo4j." in prompt
    assert "Private preface" not in prompt
    assert entities[0]["source_contexts"] == [
        {
            "property_id": "p1",
            "text": "Atlas uses Neo4j for graph retrieval and deployment.",
        }
    ]


def test_entity_context_is_limited_to_250_mixed_language_words():
    from backend.app.services import graph_store

    before = [f"before{index}" for index in range(140)] + ["知识图谱"] * 10
    after = [f"after{index}" for index in range(140)]
    text = " ".join([*before, "Atlas", *after]) + "."

    entities, _ = GraphRAGBuilder(
        schema="DocSeekEntity(name,type,definition)", prompt="Extract entities"
    ).build(
        [
            {
                "project_id": "p",
                "property_id": "p1",
                "property_type": "text",
                "text": text,
            }
        ]
    )

    atlas = next(entity for entity in entities if entity["id"] == "atlas")
    context = atlas["source_contexts"][0]["text"]
    assert "Atlas" in context
    assert graph_store._context_word_count(context) == 250


def test_local_entity_embedding_uses_the_bounded_context(monkeypatch):
    from backend.app.services import graph_store

    embedded_texts = []

    def capture_embedding(text, dimensions=32):
        embedded_texts.append(text)
        return [1.0]

    monkeypatch.setattr(graph_store, "embedding", capture_embedding)

    graph_store.extract_entities(
        [
            {
                "project_id": "p",
                "property_id": "p1",
                "text": "Atlas manages release deployments.",
            }
        ]
    )

    assert "Atlas\nAtlas manages release deployments.\nAtlas manages release deployments." in embedded_texts


def test_graphrag_builder_writes_text_with_source_metadata_through_neo4j_pipeline(monkeypatch):
    calls = []

    class FakePipeline:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        async def run_async(self, **kwargs):
            calls.append(("run", kwargs))

    import backend.app.services.graph_store as graph_store
    monkeypatch.setattr(graph_store, "Neo4jSimpleKGPipeline", FakePipeline)
    builder = GraphRAGBuilder(schema="DocSeekEntity(name,type)", prompt="Extract entities")
    builder.write_to_neo4j(
        [{"project_id": "p", "property_id": "text-1", "property_type": "text", "text": "Neo4j powers DocSeek."}],
        driver=object(),
        llm=object(),
        embedder=object(),
    )
    assert calls[0][0] == "init"
    assert calls[0][1]["neo4j_database"] == "entity_graph"
    assert calls[1] == ("run", {"text": "Neo4j powers DocSeek.", "document_metadata": {"project_id": "p", "property_id": "text-1"}})
