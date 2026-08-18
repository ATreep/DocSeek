from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Callable

import jieba

from .graph_store import embedding, embedding_for_settings, similarity


@dataclass(frozen=True)
class RetrievalConfig:
    minimum_direct_score: float = 0.35
    minimum_neighbor_score: float = 0.18
    max_seed_nodes_per_kind: int = 4
    max_neighbors_per_seed: int = 4
    max_second_hop_neighbors: int = 2
    max_nodes: int = 18
    max_relations: int = 24
    max_evidence_tokens: int = 3_000
    max_evidence_chars: int = 16_000
    hop_decay: float = 0.65
    second_hop_relation_threshold: float = 0.75
    generic_hub_degree: int = 8


_GENERIC_RELATIONS = {
    "RELATED",
    "RELATED_TO",
    "ASSOCIATED_WITH",
    "CO_OCCURS",
    "CONNECTED_TO",
}


def _tokens(value: str) -> set[str]:
    normalized = re.sub(r"[_-]+", " ", value.casefold())
    return {
        token.casefold()
        for token in jieba.cut(normalized)
        if re.search(r"[a-z0-9\u3400-\u9fff]", token, flags=re.IGNORECASE)
    }


def _label(kind: str, node: dict[str, Any]) -> str:
    value = node.get("filename") if kind == "property" else node.get("name")
    return str(value or node.get("id") or "Unknown")


def _node_text(kind: str, node: dict[str, Any]) -> str:
    fields = [_label(kind, node), str(node.get("definition") or "")]
    if kind == "property":
        fields.append(str(node.get("content") or ""))
    else:
        fields.extend(
            str(context.get("text") or "")
            for context in node.get("source_contexts", node.get("contexts", []))
            if isinstance(context, dict)
        )
    return "\n".join(field for field in fields if field)


def _semantic_score(
    query_vector: list[float], node: dict[str, Any], text: str
) -> float:
    vector = node.get("embedding")
    if not isinstance(vector, list) or not vector:
        vector = embedding(text) if len(query_vector) == 32 else []
    if len(vector) != len(query_vector):
        return 0.0
    return max(0.0, min(1.0, float(similarity(query_vector, vector))))


def _lexical_score(query: str, text: str) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    text_tokens = _tokens(text)
    overlap = len(query_tokens & text_tokens) / len(query_tokens)
    normalized_query = re.sub(r"\s+", " ", query.casefold()).strip()
    phrase_bonus = (
        0.15 if normalized_query and normalized_query in text.casefold() else 0.0
    )
    return min(1.0, overlap + phrase_bonus)


def _relation_relevance(query: str, relation_type: str) -> float:
    normalized = str(relation_type or "RELATED").upper()
    base = 0.24 if normalized in _GENERIC_RELATIONS else 0.8
    query_tokens = _tokens(query)
    relation_tokens = _tokens(normalized)
    if query_tokens and relation_tokens:
        base += 0.2 * len(query_tokens & relation_tokens) / len(relation_tokens)
    return min(1.0, base)


def _degree_map(graph: dict[str, Any]) -> dict[str, int]:
    degrees: dict[str, int] = {}
    for edge in graph.get("edges", []):
        for endpoint in (edge.get("source"), edge.get("target")):
            node_id = str(endpoint or "")
            if node_id:
                degrees[node_id] = degrees.get(node_id, 0) + 1
    return degrees


def _token_count(value: str) -> int:
    return sum(
        1
        for token in jieba.cut(value)
        if re.search(r"[a-z0-9\u3400-\u9fff]", token, flags=re.IGNORECASE)
    )


class GraphRetriever:
    def __init__(
        self,
        graph_store: Any,
        *,
        config: RetrievalConfig | None = None,
        embed_query: Callable[[str], list[float]] | None = None,
    ):
        self.graph_store = graph_store
        self.config = config or RetrievalConfig()
        self._embed_query = embed_query

    def _query_vector(self, query: str) -> list[float]:
        if self._embed_query:
            return self._embed_query(query)
        settings = getattr(self.graph_store, "settings", None)
        if settings is not None:
            return embedding_for_settings(query, settings)
        return embedding(query)

    def _graphs(
        self, project_id: str, allowed_kinds: set[str] | None = None
    ) -> dict[str, dict[str, Any]]:
        return {
            kind: (
                self.graph_store.graph(project_id, kind)
                if allowed_kinds is None or kind in allowed_kinds
                else {"nodes": [], "edges": []}
            )
            for kind in ("property", "entity")
        }

    def find_seeds(
        self,
        query: str,
        query_vector: list[float],
        graphs: dict[str, dict[str, Any]],
        allowed_kinds: set[str] | None = None,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        seeds: dict[tuple[str, str], dict[str, Any]] = {}
        for kind, graph in graphs.items():
            if allowed_kinds is not None and kind not in allowed_kinds:
                continue
            ranked = []
            for node in graph.get("nodes", []):
                node_id = str(node.get("id") or "")
                if not node_id:
                    continue
                text = _node_text(kind, node)
                semantic = _semantic_score(query_vector, node, text)
                lexical = _lexical_score(query, text)
                score = 0.65 * semantic + 0.35 * lexical
                if score < self.config.minimum_direct_score:
                    continue
                ranked.append(
                    {
                        "kind": kind,
                        "node": node,
                        "score": score,
                        "semantic_score": semantic,
                        "lexical_score": lexical,
                        "seed_id": node_id,
                        "seed_kind": kind,
                        "path": [_label(kind, node)],
                        "reason": "Direct match",
                        "direct": True,
                    }
                )
            ranked.sort(key=lambda item: item["score"], reverse=True)
            for item in ranked[: self.config.max_seed_nodes_per_kind]:
                seeds[(kind, str(item["node"]["id"]))] = item
        return seeds

    @staticmethod
    def _relation(
        kind: str, edge: dict[str, Any], nodes_by_id: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        source_id = str(edge.get("source") or "")
        target_id = str(edge.get("target") or "")
        return {
            "source": source_id,
            "source_label": _label(
                kind, nodes_by_id.get(source_id, {"id": source_id})
            ),
            "source_kind": kind,
            "type": str(edge.get("type") or "RELATED"),
            "target": target_id,
            "target_label": _label(
                kind, nodes_by_id.get(target_id, {"id": target_id})
            ),
            "target_kind": kind,
        }

    def _expand_graph(
        self,
        project_id: str,
        kind: str,
        query: str,
        query_vector: list[float],
        graph: dict[str, Any],
        seeds: dict[tuple[str, str], dict[str, Any]],
    ) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
        candidates = dict(seeds)
        traversed: list[dict[str, Any]] = []
        nodes_by_id = {
            str(node.get("id")): node for node in graph.get("nodes", [])
        }
        degrees = _degree_map(graph)

        def consider(
            seed: dict[str, Any],
            edge: dict[str, Any],
            neighbor_id: str,
            path: list[str],
            path_relation: str,
            hop_count: int,
            relation_strength: float,
        ) -> dict[str, Any] | None:
            node = nodes_by_id.get(neighbor_id)
            if node is None:
                return None
            text = _node_text(kind, node)
            semantic = _semantic_score(query_vector, node, text)
            score = (
                seed["score"]
                * relation_strength
                * self.config.hop_decay**hop_count
                + 0.25 * semantic
            )
            degree = degrees.get(neighbor_id, 0)
            if degree > 2:
                score /= math.sqrt(1 + (degree - 2) / 3)
            if score < self.config.minimum_neighbor_score:
                return None
            relation_type = str(edge.get("type") or "RELATED")
            item = {
                "kind": kind,
                "node": node,
                "score": score,
                "semantic_score": semantic,
                "lexical_score": _lexical_score(query, text),
                "seed_id": seed["seed_id"],
                "seed_kind": seed["seed_kind"],
                "path": [*path, path_relation, _label(kind, node)],
                "reason": f"Related through {relation_type}",
                "direct": False,
            }
            key = (kind, neighbor_id)
            previous = candidates.get(key)
            if previous is None or (
                not previous["direct"] and score > previous["score"]
            ):
                candidates[key] = item
            return item

        kind_seeds = [item for item in seeds.values() if item["kind"] == kind]
        for seed in kind_seeds:
            seed_id = str(seed["node"]["id"])
            neighborhood = self.graph_store.neighbors(
                project_id, kind, [seed_id]
            )
            first_hops = []
            for edge in neighborhood.get("edges", []):
                source = str(edge.get("source") or "")
                target = str(edge.get("target") or "")
                neighbor_id = target if source == seed_id else source if target == seed_id else ""
                if not neighbor_id:
                    continue
                relation_type = str(edge.get("type") or "RELATED")
                path_relation = (
                    relation_type
                    if source == seed_id
                    else f"{relation_type} (incoming)"
                )
                strength = _relation_relevance(query, relation_type)
                first_hops.append((strength, edge, neighbor_id, path_relation))
            first_hops.sort(key=lambda item: item[0], reverse=True)
            for strength, edge, neighbor_id, path_relation in first_hops[
                : self.config.max_neighbors_per_seed
            ]:
                first = consider(
                    seed,
                    edge,
                    neighbor_id,
                    seed["path"],
                    path_relation,
                    1,
                    strength,
                )
                if first is None:
                    continue
                traversed.append(self._relation(kind, edge, nodes_by_id))
                if (
                    strength < self.config.second_hop_relation_threshold
                    or degrees.get(neighbor_id, 0) > self.config.generic_hub_degree
                ):
                    continue
                second_neighborhood = self.graph_store.neighbors(
                    project_id, kind, [neighbor_id]
                )
                second_hops = []
                for second_edge in second_neighborhood.get("edges", []):
                    source = str(second_edge.get("source") or "")
                    target = str(second_edge.get("target") or "")
                    second_id = target if source == neighbor_id else source if target == neighbor_id else ""
                    if not second_id or second_id == seed_id:
                        continue
                    second_type = str(second_edge.get("type") or "RELATED")
                    second_path_relation = (
                        second_type
                        if source == neighbor_id
                        else f"{second_type} (incoming)"
                    )
                    second_strength = _relation_relevance(
                        query, second_type
                    )
                    second_hops.append(
                        (
                            second_strength,
                            second_edge,
                            second_id,
                            second_path_relation,
                        )
                    )
                second_hops.sort(key=lambda item: item[0], reverse=True)
                for (
                    second_strength,
                    second_edge,
                    second_id,
                    second_path_relation,
                ) in second_hops[
                    : self.config.max_second_hop_neighbors
                ]:
                    second = consider(
                        seed,
                        second_edge,
                        second_id,
                        first["path"],
                        second_path_relation,
                        2,
                        strength * second_strength,
                    )
                    if second is not None:
                        traversed.append(
                            self._relation(kind, second_edge, nodes_by_id)
                        )
        return candidates, traversed

    def expand_entity_graph(self, *args, **kwargs):
        return self._expand_graph(*args, kind="entity", **kwargs)

    def expand_property_graph(self, *args, **kwargs):
        return self._expand_graph(*args, kind="property", **kwargs)

    def bridge_properties_and_entities(
        self,
        query: str,
        query_vector: list[float],
        graphs: dict[str, dict[str, Any]],
        candidates: dict[tuple[str, str], dict[str, Any]],
    ) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
        bridged = dict(candidates)
        relations: list[dict[str, Any]] = []
        properties = {
            str(node.get("id")): node
            for node in graphs["property"].get("nodes", [])
        }
        entities = {
            str(node.get("id")): node
            for node in graphs["entity"].get("nodes", [])
        }
        for entity_id, entity in entities.items():
            source_ids = [
                str(value) for value in entity.get("source_property_ids", [])
            ]
            for property_id in source_ids:
                property_node = properties.get(property_id)
                if property_node is None:
                    continue
                entity_item = bridged.get(("entity", entity_id))
                property_item = bridged.get(("property", property_id))
                extracted_from_relation = {
                    "source": entity_id,
                    "source_label": _label("entity", entity),
                    "source_kind": "entity",
                    "type": "EXTRACTED_FROM",
                    "target": property_id,
                    "target_label": _label("property", property_node),
                    "target_kind": "property",
                }
                if entity_item is not None and property_item is None:
                    semantic = _semantic_score(
                        query_vector,
                        property_node,
                        _node_text("property", property_node),
                    )
                    score = (
                        entity_item["score"] * 0.75 * self.config.hop_decay
                        + 0.25 * semantic
                    )
                    if score >= self.config.minimum_neighbor_score:
                        bridged[("property", property_id)] = {
                            "kind": "property",
                            "node": property_node,
                            "score": score,
                            "semantic_score": semantic,
                            "lexical_score": _lexical_score(
                                query, _node_text("property", property_node)
                            ),
                            "seed_id": entity_item["seed_id"],
                            "seed_kind": entity_item["seed_kind"],
                            "path": [
                                *entity_item["path"],
                                "EXTRACTED_FROM",
                                _label("property", property_node),
                            ],
                            "reason": "Related through EXTRACTED_FROM",
                            "direct": False,
                        }
                        relations.append(extracted_from_relation)
                elif property_item is not None and entity_item is None:
                    semantic = _semantic_score(
                        query_vector, entity, _node_text("entity", entity)
                    )
                    score = (
                        property_item["score"] * 0.75 * self.config.hop_decay
                        + 0.25 * semantic
                    )
                    if score >= self.config.minimum_neighbor_score:
                        bridged[("entity", entity_id)] = {
                            "kind": "entity",
                            "node": entity,
                            "score": score,
                            "semantic_score": semantic,
                            "lexical_score": _lexical_score(
                                query, _node_text("entity", entity)
                            ),
                            "seed_id": property_item["seed_id"],
                            "seed_kind": property_item["seed_kind"],
                            "path": [
                                *property_item["path"],
                                "HAS_ENTITY",
                                _label("entity", entity),
                            ],
                            "reason": "Related through HAS_ENTITY",
                            "direct": False,
                        }
                        relations.append(
                            {
                                "source": property_id,
                                "source_label": _label(
                                    "property", property_node
                                ),
                                "source_kind": "property",
                                "type": "HAS_ENTITY",
                                "target": entity_id,
                                "target_label": _label("entity", entity),
                                "target_kind": "entity",
                            }
                        )
                elif entity_item is not None and property_item is not None:
                    relations.append(extracted_from_relation)
        return bridged, relations

    def rerank_subgraph(
        self,
        candidates: dict[tuple[str, str], dict[str, Any]],
        relations: list[dict[str, Any]],
        *,
        property_limit: int,
        entity_limit: int,
        total_limit: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        ranked = sorted(
            candidates.values(),
            key=lambda item: (not item["direct"], -item["score"]),
        )
        selected: list[dict[str, Any]] = []
        selected_keys: set[tuple[str, str]] = set()
        kind_counts = {"property": 0, "entity": 0}
        kind_limits = {
            "property": max(0, property_limit),
            "entity": max(0, entity_limit),
        }
        total_limit = max(0, total_limit)
        used_chars = 0
        used_tokens = 0
        for item in ranked:
            kind = item["kind"]
            if (
                kind_counts[kind] >= kind_limits[kind]
                or len(selected) >= total_limit
            ):
                continue
            serialized_evidence = json.dumps(
                {
                    "label": _label(kind, item["node"]),
                    "definition": item["node"].get("definition"),
                    "contexts": item["node"].get("source_contexts", []),
                    "path": item["path"],
                },
                ensure_ascii=False,
            )
            evidence_size = len(serialized_evidence)
            evidence_tokens = _token_count(serialized_evidence)
            if selected and (
                used_chars + evidence_size > self.config.max_evidence_chars
                or used_tokens + evidence_tokens > self.config.max_evidence_tokens
            ):
                continue
            selected.append(item)
            selected_keys.add((kind, str(item["node"]["id"])))
            kind_counts[kind] += 1
            used_chars += evidence_size
            used_tokens += evidence_tokens
        bounded_relations = []
        seen_relations = set()
        for relation in relations:
            source_key = (relation["source_kind"], str(relation["source"]))
            target_key = (relation["target_kind"], str(relation["target"]))
            key = (source_key, relation["type"], target_key)
            if (
                source_key not in selected_keys
                or target_key not in selected_keys
                or key in seen_relations
            ):
                continue
            seen_relations.add(key)
            bounded_relations.append(relation)
            if len(bounded_relations) >= self.config.max_relations:
                break
        return selected, bounded_relations

    @staticmethod
    def _public_node(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": item["kind"],
            **item["node"],
            "score": round(item["score"], 4),
            "semantic_score": round(item["semantic_score"], 4),
            "lexical_score": round(item["lexical_score"], 4),
            "retrieval_reason": item["reason"],
            "retrieval_path": item["path"],
        }

    @staticmethod
    def _retrieval_path(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "seed": item["path"][0],
            "seed_id": item["seed_id"],
            "seed_kind": item["seed_kind"],
            "target": _label(item["kind"], item["node"]),
            "target_id": str(item["node"].get("id")),
            "target_kind": item["kind"],
            "path": item["path"],
            "score": round(item["score"], 4),
        }

    @staticmethod
    def _property_relations(
        relations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "source": relation["source"],
                "target": relation["target"],
                "type": relation["type"],
                "source_filename": relation["source_label"],
                "target_filename": relation["target_label"],
            }
            for relation in relations
            if relation["source_kind"] == relation["target_kind"] == "property"
        ]

    @staticmethod
    def _attach_property_relations(
        properties: list[dict[str, Any]], relations: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        enriched = []
        for item in properties:
            item_relations = []
            for relation in relations:
                if (
                    relation["source_kind"] != "property"
                    or relation["target_kind"] != "property"
                ):
                    continue
                if relation["source"] == item["id"]:
                    direction = "outgoing"
                    related_id = relation["target"]
                    related_label = relation["target_label"]
                elif relation["target"] == item["id"]:
                    direction = "incoming"
                    related_id = relation["source"]
                    related_label = relation["source_label"]
                else:
                    continue
                item_relations.append(
                    {
                        "source": relation["source"],
                        "target": relation["target"],
                        "type": relation["type"],
                        "source_filename": relation["source_label"],
                        "target_filename": relation["target_label"],
                        "direction": direction,
                        "related_property_id": related_id,
                        "related_property_filename": related_label,
                    }
                )
            enriched.append({**item, "relations": item_relations})
        return enriched

    def build_evidence_context(
        self,
        project_id: str,
        query: str,
        limit: int = 10,
        allowed_kinds: set[str] | None = None,
        *,
        property_limit: int | None = None,
        entity_limit: int | None = None,
        total_limit: int | None = None,
    ) -> dict[str, Any]:
        property_limit = limit if property_limit is None else property_limit
        entity_limit = limit if entity_limit is None else entity_limit
        if total_limit is None:
            total_limit = min(
                self.config.max_nodes,
                property_limit + entity_limit,
            )
        graphs = self._graphs(project_id, allowed_kinds)
        query_vector = self._query_vector(query)
        seeds = self.find_seeds(query, query_vector, graphs, allowed_kinds)
        candidates, property_relations = self.expand_property_graph(
            project_id,
            query=query,
            query_vector=query_vector,
            graph=graphs["property"],
            seeds=seeds,
        )
        candidates, entity_relations = self.expand_entity_graph(
            project_id,
            query=query,
            query_vector=query_vector,
            graph=graphs["entity"],
            seeds=candidates,
        )
        if allowed_kinds is None or allowed_kinds == {"property", "entity"}:
            candidates, bridge_relations = self.bridge_properties_and_entities(
                query, query_vector, graphs, candidates
            )
        else:
            bridge_relations = []
        selected, relations = self.rerank_subgraph(
            candidates,
            [*property_relations, *entity_relations, *bridge_relations],
            property_limit=property_limit,
            entity_limit=entity_limit,
            total_limit=total_limit,
        )
        public_nodes = [self._public_node(item) for item in selected]
        properties = self._attach_property_relations(
            [item for item in public_nodes if item["kind"] == "property"],
            relations,
        )
        entities = [item for item in public_nodes if item["kind"] == "entity"]
        return {
            "properties": properties,
            "entities": entities,
            "relations": relations,
            "retrieval_paths": [
                self._retrieval_path(item) for item in selected
            ],
            "property_relations": self._property_relations(relations),
        }

    def search(
        self,
        project_id: str,
        query: str,
        limit: int = 10,
        allowed_kinds: set[str] | None = None,
        *,
        property_limit: int | None = None,
        entity_limit: int | None = None,
        total_limit: int | None = None,
    ) -> dict[str, Any]:
        return self.build_evidence_context(
            project_id,
            query,
            limit,
            allowed_kinds,
            property_limit=property_limit,
            entity_limit=entity_limit,
            total_limit=total_limit,
        )

    def search_properties(
        self, project_id: str, query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        return self.search(
            project_id,
            query,
            limit,
            {"property"},
            property_limit=limit,
            entity_limit=0,
            total_limit=limit,
        )["properties"]

    def search_entities(
        self, project_id: str, query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        return self.search(
            project_id,
            query,
            limit,
            {"entity"},
            property_limit=0,
            entity_limit=limit,
            total_limit=limit,
        )["entities"]

    def context(
        self,
        project_id: str,
        query: str,
        limit: int = 5,
        *,
        property_limit: int | None = None,
        entity_limit: int | None = None,
        total_limit: int | None = None,
    ) -> dict[str, Any]:
        return self.build_evidence_context(
            project_id,
            query,
            limit,
            property_limit=property_limit,
            entity_limit=entity_limit,
            total_limit=total_limit,
        )


# Compatibility for integrations that imported the original class name.
Retriever = GraphRetriever
