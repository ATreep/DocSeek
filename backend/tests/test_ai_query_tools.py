from backend.app.services.ai_query_tools import AIQueryTools


class StaticGraphStore:
    def __init__(self):
        self.graphs = {
            "property": {
                "nodes": [
                    {
                        "id": "manual",
                        "filename": "manual.md",
                        "definition": "An Atlas installation manual.",
                    },
                    {
                        "id": "architecture",
                        "filename": "architecture.pdf",
                        "definition": "The Atlas system architecture.",
                    },
                ],
                "edges": [
                    {
                        "source": "manual",
                        "target": "architecture",
                        "type": "DOCUMENTS",
                    }
                ],
            },
            "entity": {
                "nodes": [
                    {
                        "id": "atlas",
                        "name": "Atlas",
                        "definition": "A document intelligence product.",
                        "source_property_ids": ["manual"],
                    },
                    {
                        "id": "neo4j",
                        "name": "Neo4j",
                        "definition": "A graph database used by Atlas.",
                        "source_property_ids": ["architecture"],
                    },
                ],
                "edges": [
                    {"source": "atlas", "target": "neo4j", "type": "USES"}
                ],
            },
        }

    def graph(self, project_id, kind):
        assert project_id == "project"
        return self.graphs[kind]


class StaticCatalog:
    def list(self, project_id):
        assert project_id == "project"
        return [
            {
                "id": "manual",
                "filename": "manual.md",
                "directory": "Products/Atlas",
                "definition": "An Atlas installation manual.",
                "property_type": "markdown",
            },
            {
                "id": "architecture",
                "filename": "architecture.pdf",
                "directory": "Products/Atlas/Engineering",
                "definition": "The Atlas system architecture.",
                "property_type": "pdf",
            },
        ]


class StaticRetriever:
    def search_entities(self, project_id, query, limit):
        assert (project_id, query, limit) == ("project", "Atlas", 2)
        return [
            {"id": "atlas", "name": "Atlas", "score": 0.91, "definition": "hidden"},
            {"id": "neo4j", "name": "Neo4j", "score": 0.73, "definition": "hidden"},
        ]

    def search_properties(self, project_id, query, limit):
        assert (project_id, query, limit) == ("project", "manual", 2)
        return [
            {
                "id": "manual",
                "filename": "manual.md",
                "score": 0.87,
                "content": "must not leak",
            },
            {
                "id": "architecture",
                "filename": "architecture.pdf",
                "score": 0.64,
                "content": "must not leak",
            },
        ]


def ai_query_tools():
    return AIQueryTools(
        "project",
        StaticGraphStore(),
        StaticCatalog(),
        retriever=StaticRetriever(),
    )


def test_query_tools_return_only_score_name_and_identifier():
    tools = ai_query_tools()

    assert tools.query_entities("Atlas", 2) == {
        "entities": [
            {"score": 0.91, "name": "Atlas", "identifier": "atlas"},
            {"score": 0.73, "name": "Neo4j", "identifier": "neo4j"},
        ]
    }
    assert tools.query_properties("manual", 2) == {
        "properties": [
            {"score": 0.87, "name": "manual.md", "identifier": "manual"},
            {
                "score": 0.64,
                "name": "architecture.pdf",
                "identifier": "architecture",
            },
        ]
    }


def test_get_entity_detail_returns_definition_relations_and_source_properties():
    detail = ai_query_tools().get_entity_detail("atlas")

    assert detail == {
        "identifier": "atlas",
        "name": "Atlas",
        "definition": "A document intelligence product.",
        "relations": [
            {
                "direction": "outgoing",
                "type": "USES",
                "related_identifier": "neo4j",
                "related_name": "Neo4j",
            }
        ],
        "source_properties": [
            {
                "identifier": "manual",
                "filename": "manual.md",
                "definition": "An Atlas installation manual.",
            }
        ],
    }


def test_get_property_detail_returns_definition_relations_and_owned_entities():
    detail = ai_query_tools().get_property_detail("architecture")

    assert detail == {
        "identifier": "architecture",
        "name": "architecture.pdf",
        "definition": "The Atlas system architecture.",
        "relations": [
            {
                "direction": "incoming",
                "type": "DOCUMENTS",
                "related_identifier": "manual",
                "related_name": "manual.md",
            }
        ],
        "owned_entities": [
            {
                "identifier": "neo4j",
                "name": "Neo4j",
                "definition": "A graph database used by Atlas.",
            }
        ],
    }


def test_property_group_tree_returns_hierarchy_with_property_ids_and_names():
    assert ai_query_tools().property_group_tree() == {
        "group_name": "",
        "properties": [],
        "groups": [
            {
                "group_name": "Products",
                "properties": [],
                "groups": [
                    {
                        "group_name": "Atlas",
                        "properties": [
                            {
                                "property_id": "manual",
                                "property_name": "manual.md",
                            }
                        ],
                        "groups": [
                            {
                                "group_name": "Engineering",
                                "properties": [
                                    {
                                        "property_id": "architecture",
                                        "property_name": "architecture.pdf",
                                    }
                                ],
                                "groups": [],
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_execute_validates_arguments_and_reports_missing_nodes():
    tools = ai_query_tools()

    assert tools.execute("query_entities", {"query": ""}) == {
        "error": "query must be a non-empty string"
    }
    assert tools.execute("get_entity_detail", {"entity_id": "missing"}) == {
        "error": "Entity not found",
        "entity_id": "missing",
    }
    assert tools.execute("unknown", {}) == {"error": "Unsupported AI Query tool."}
