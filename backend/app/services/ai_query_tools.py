from __future__ import annotations

from typing import Any

from .pipeline import _current_group_tree
from .retrieval import GraphRetriever


MAX_QUERY_TOOL_RESULTS = 20


def _node_label(kind: str, node: dict[str, Any]) -> str:
    field = "filename" if kind == "property" else "name"
    return str(node.get(field) or node.get("id") or "")


def _entity_belongs_to_property(entity: dict[str, Any], property_id: str) -> bool:
    source_ids = entity.get("source_property_ids")
    if isinstance(source_ids, list) and property_id in {
        str(item) for item in source_ids
    }:
        return True
    contexts = entity.get("source_contexts", entity.get("contexts", []))
    return isinstance(contexts, list) and any(
        isinstance(context, dict)
        and str(context.get("property_id") or "") == property_id
        for context in contexts
    )


class AIQueryTools:
    def __init__(
        self,
        project_id: str,
        graph_store: Any,
        catalog: Any,
        *,
        retriever: Any | None = None,
    ):
        self.project_id = project_id
        self.graph_store = graph_store
        self.catalog = catalog
        self.retriever = retriever or GraphRetriever(graph_store)

    @staticmethod
    def _query(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("query must be a non-empty string")
        return value.strip()

    @staticmethod
    def _identifier(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _limit(value: Any) -> int:
        if value is None:
            return 5
        if isinstance(value, bool):
            return 5
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 5
        return max(1, min(MAX_QUERY_TOOL_RESULTS, parsed))

    def query_entities(self, query: str, max_result: int = 5) -> dict[str, Any]:
        normalized_query = self._query(query)
        limit = self._limit(max_result)
        results = self.retriever.search_entities(
            self.project_id, normalized_query, limit=limit
        )
        return {
            "entities": [
                {
                    "score": item.get("score"),
                    "name": _node_label("entity", item),
                    "identifier": str(item.get("id") or ""),
                }
                for item in results[:limit]
                if item.get("id")
            ]
        }

    def query_properties(
        self, query: str, max_result: int = 5
    ) -> dict[str, Any]:
        normalized_query = self._query(query)
        limit = self._limit(max_result)
        results = self.retriever.search_properties(
            self.project_id, normalized_query, limit=limit
        )
        return {
            "properties": [
                {
                    "score": item.get("score"),
                    "name": _node_label("property", item),
                    "identifier": str(item.get("id") or ""),
                }
                for item in results[:limit]
                if item.get("id")
            ]
        }

    @staticmethod
    def _relations(
        kind: str,
        node_id: str,
        graph: dict[str, Any],
    ) -> list[dict[str, str]]:
        nodes_by_id = {
            str(node.get("id") or ""): node for node in graph.get("nodes", [])
        }
        relations = []
        for edge in graph.get("edges", []):
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            if source == node_id:
                direction = "outgoing"
                related_id = target
            elif target == node_id:
                direction = "incoming"
                related_id = source
            else:
                continue
            relations.append(
                {
                    "direction": direction,
                    "type": str(edge.get("type") or "RELATED"),
                    "related_identifier": related_id,
                    "related_name": _node_label(
                        kind, nodes_by_id.get(related_id, {"id": related_id})
                    ),
                }
            )
        return relations

    def get_entity_detail(self, entity_id: str) -> dict[str, Any]:
        normalized_id = self._identifier(entity_id, "entity_id")
        entity_graph = self.graph_store.graph(self.project_id, "entity")
        entity = next(
            (
                item
                for item in entity_graph.get("nodes", [])
                if str(item.get("id") or "") == normalized_id
            ),
            None,
        )
        if entity is None:
            return {"error": "Entity not found", "entity_id": normalized_id}

        property_graph = self.graph_store.graph(self.project_id, "property")
        properties_by_id = {
            str(item.get("id") or ""): item
            for item in property_graph.get("nodes", [])
        }
        catalog_by_id = {
            str(item.get("id") or ""): item
            for item in self.catalog.list(self.project_id)
        }
        source_ids = [
            str(item)
            for item in entity.get("source_property_ids", [])
            if str(item)
        ]
        for context in entity.get("source_contexts", entity.get("contexts", [])):
            if not isinstance(context, dict):
                continue
            property_id = str(context.get("property_id") or "")
            if property_id and property_id not in source_ids:
                source_ids.append(property_id)
        source_properties = []
        for property_id in source_ids:
            item = properties_by_id.get(property_id) or catalog_by_id.get(property_id)
            if item is None:
                continue
            source_properties.append(
                {
                    "identifier": property_id,
                    "filename": str(item.get("filename") or property_id),
                    "definition": str(item.get("definition") or ""),
                }
            )
        return {
            "identifier": normalized_id,
            "name": _node_label("entity", entity),
            "definition": str(entity.get("definition") or ""),
            "relations": self._relations("entity", normalized_id, entity_graph),
            "source_properties": source_properties,
        }

    def get_property_detail(self, property_id: str) -> dict[str, Any]:
        normalized_id = self._identifier(property_id, "property_id")
        property_graph = self.graph_store.graph(self.project_id, "property")
        property_node = next(
            (
                item
                for item in property_graph.get("nodes", [])
                if str(item.get("id") or "") == normalized_id
            ),
            None,
        )
        catalog_item = next(
            (
                item
                for item in self.catalog.list(self.project_id)
                if str(item.get("id") or "") == normalized_id
            ),
            None,
        )
        item = property_node or catalog_item
        if item is None:
            return {"error": "Property not found", "property_id": normalized_id}

        entity_graph = self.graph_store.graph(self.project_id, "entity")
        owned_entities = [
            {
                "identifier": str(entity.get("id") or ""),
                "name": _node_label("entity", entity),
                "definition": str(entity.get("definition") or ""),
            }
            for entity in entity_graph.get("nodes", [])
            if entity.get("id")
            and _entity_belongs_to_property(entity, normalized_id)
        ]
        return {
            "identifier": normalized_id,
            "name": _node_label("property", item),
            "definition": str(item.get("definition") or ""),
            "relations": self._relations(
                "property", normalized_id, property_graph
            ),
            "owned_entities": owned_entities,
        }

    @staticmethod
    def _public_group_tree(group: dict[str, Any]) -> dict[str, Any]:
        return {
            "group_name": str(group.get("group_name") or ""),
            "properties": [
                {"filename": str(item.get("filename") or "")}
                for item in group.get("properties", [])
                if item.get("filename")
            ],
            "groups": [
                AIQueryTools._public_group_tree(child)
                for child in group.get("groups", [])
                if isinstance(child, dict)
            ],
        }

    def get_property_group_tree(self) -> dict[str, Any]:
        tree = _current_group_tree(self.catalog.list(self.project_id))
        return self._public_group_tree(tree)

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            return {"error": f"Invalid {name} arguments."}
        try:
            if name == "query_entities":
                return self.query_entities(
                    arguments.get("query"), arguments.get("max_result", 5)
                )
            if name == "query_properties":
                return self.query_properties(
                    arguments.get("query"), arguments.get("max_result", 5)
                )
            if name == "get_entity_detail":
                return self.get_entity_detail(arguments.get("entity_id"))
            if name == "get_property_detail":
                return self.get_property_detail(arguments.get("property_id"))
            if name == "get_property_group_tree":
                return self.get_property_group_tree()
        except ValueError as exc:
            return {"error": str(exc)}
        return {"error": "Unsupported AI Query tool."}
