from typing import Any

from .graph_store import Neo4jGraphStore


class Retriever:
    def __init__(self, graph_store: Neo4jGraphStore):
        self.graph_store = graph_store

    @staticmethod
    def _enrich_properties(
        properties: list[dict[str, Any]], property_graph: dict[str, Any]
    ) -> list[dict[str, Any]]:
        filenames = {
            node.get("id"): node.get("filename")
            for node in property_graph.get("nodes", [])
        }
        enriched = []
        for item in properties:
            property_id = item.get("id")
            relations = []
            for edge in property_graph.get("edges", []):
                source = edge.get("source")
                target = edge.get("target")
                if source == property_id:
                    direction = "outgoing"
                    related_id = target
                elif target == property_id:
                    direction = "incoming"
                    related_id = source
                else:
                    continue
                relations.append(
                    {
                        "source": source,
                        "target": target,
                        "type": edge.get("type"),
                        "source_filename": filenames.get(source),
                        "target_filename": filenames.get(target),
                        "direction": direction,
                        "related_property_id": related_id,
                        "related_property_filename": filenames.get(related_id),
                    }
                )
            enriched.append({**item, "relations": relations})
        return enriched

    @staticmethod
    def _deduplicated_relations(
        properties: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        relations = []
        seen = set()
        for item in properties:
            for relation in item.get("relations", []):
                key = (
                    relation.get("source"),
                    relation.get("target"),
                    relation.get("type"),
                )
                if key in seen:
                    continue
                seen.add(key)
                relations.append(
                    {
                        "source": relation.get("source"),
                        "target": relation.get("target"),
                        "type": relation.get("type"),
                        "source_filename": relation.get("source_filename"),
                        "target_filename": relation.get("target_filename"),
                    }
                )
        return relations

    def search_properties(
        self, project_id: str, query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        properties = self.graph_store.search(
            project_id, query, "properties", limit
        )
        property_graph = self.graph_store.graph(project_id, "property")
        return self._enrich_properties(properties, property_graph)

    def search(
        self, project_id: str, query: str, limit: int = 10
    ) -> dict[str, list[dict[str, Any]]]:
        return {
            "properties": self.search_properties(project_id, query, limit),
            "entities": self.graph_store.search(project_id, query, "entities", limit),
        }

    def context(
        self, project_id: str, query: str, limit: int = 5
    ) -> dict[str, Any]:
        grouped = self.search(project_id, query, limit)
        return {
            "properties": grouped["properties"],
            "entities": grouped["entities"],
            "property_relations": self._deduplicated_relations(
                grouped["properties"]
            ),
            "property_graph": self.graph_store.graph(project_id, "property"),
            "entity_graph": self.graph_store.graph(project_id, "entity"),
        }
