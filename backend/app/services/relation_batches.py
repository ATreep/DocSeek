from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence


COLLECTION_SIZE = 10


@dataclass(frozen=True)
class CollectionPair:
    left: tuple[dict[str, Any], ...]
    right: tuple[dict[str, Any], ...] = ()
    kind: str = "within"

    @property
    def is_cross(self) -> bool:
        return bool(self.right)


def chunk_nodes(
    nodes: Sequence[dict[str, Any]], size: int = COLLECTION_SIZE
) -> list[tuple[dict[str, Any], ...]]:
    if size < 1:
        raise ValueError("collection size must be positive")
    return [tuple(nodes[index : index + size]) for index in range(0, len(nodes), size)]


def relation_call_specs(
    new_nodes: Sequence[dict[str, Any]],
    old_nodes: Sequence[dict[str, Any]],
) -> tuple[list[CollectionPair], list[CollectionPair]]:
    new_collections = chunk_nodes(new_nodes)
    old_collections = chunk_nodes(old_nodes)
    within = [CollectionPair(collection) for collection in new_collections]
    cross = [
        CollectionPair(new_collections[left], new_collections[right], "new-new")
        for left in range(len(new_collections))
        for right in range(left + 1, len(new_collections))
    ]
    cross.extend(
        CollectionPair(new_collection, old_collection, "new-old")
        for new_collection in new_collections
        for old_collection in old_collections
    )
    return within, cross


def merge_call_specs(
    new_nodes: Sequence[dict[str, Any]],
    old_nodes: Sequence[dict[str, Any]],
) -> list[CollectionPair]:
    within, cross = relation_call_specs(new_nodes, old_nodes)
    return [*within, *cross]


def _merge_metadata(
    canonical: dict[str, Any],
    members: Iterable[dict[str, Any]],
    *,
    context_word_count: Callable[[str], int] | None = None,
    context_word_limit: int = 250,
) -> dict[str, Any]:
    merged = dict(canonical)
    sources: list[str] = []
    contexts: list[dict[str, Any]] = []
    seen_contexts: set[tuple[str, str]] = set()
    used_words = 0
    for member in members:
        for source_id in member.get("source_property_ids") or []:
            source_id = str(source_id or "")
            if source_id and source_id not in sources:
                sources.append(source_id)
        for context in member.get("source_contexts") or []:
            if not isinstance(context, dict):
                continue
            property_id = str(context.get("property_id") or "")
            text = str(context.get("text") or "").strip()
            key = (property_id, text)
            word_count = context_word_count(text) if context_word_count else len(text.split())
            if (
                property_id
                and text
                and key not in seen_contexts
                and used_words + word_count <= context_word_limit
            ):
                contexts.append({"property_id": property_id, "text": text})
                seen_contexts.add(key)
                used_words += word_count
    merged["source_property_ids"] = sources
    merged["source_contexts"] = contexts
    return merged


def consolidate_exact_entity_ids(
    old_entities: Sequence[dict[str, Any]],
    new_entities: Sequence[dict[str, Any]],
    *,
    context_word_count: Callable[[str], int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    old = [dict(entity) for entity in old_entities]
    old_indexes = {
        str(entity.get("id") or ""): index
        for index, entity in enumerate(old)
        if entity.get("id")
    }
    remaining_new: list[dict[str, Any]] = []
    for entity in new_entities:
        entity_id = str(entity.get("id") or "")
        old_index = old_indexes.get(entity_id)
        if old_index is None:
            remaining_new.append(dict(entity))
            continue
        old[old_index] = _merge_metadata(
            old[old_index],
            [old[old_index], entity],
            context_word_count=context_word_count,
        )
    return old, remaining_new


def _strongly_connected_components(
    node_ids: Sequence[str], adjacency: dict[str, set[str]]
) -> list[set[str]]:
    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[set[str]] = []

    def visit(node_id: str) -> None:
        nonlocal index
        indexes[node_id] = index
        lowlinks[node_id] = index
        index += 1
        stack.append(node_id)
        on_stack.add(node_id)
        for target_id in adjacency.get(node_id, set()):
            if target_id not in indexes:
                visit(target_id)
                lowlinks[node_id] = min(lowlinks[node_id], lowlinks[target_id])
            elif target_id in on_stack:
                lowlinks[node_id] = min(lowlinks[node_id], indexes[target_id])
        if lowlinks[node_id] != indexes[node_id]:
            return
        component: set[str] = set()
        while stack:
            member_id = stack.pop()
            on_stack.remove(member_id)
            component.add(member_id)
            if member_id == node_id:
                break
        components.append(component)

    for node_id in node_ids:
        if node_id not in indexes:
            visit(node_id)
    return components


def apply_entity_merges(
    old_entities: Sequence[dict[str, Any]],
    new_entities: Sequence[dict[str, Any]],
    proposals: Iterable[tuple[str, str]],
    *,
    context_word_count: Callable[[str], int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    old, new = consolidate_exact_entity_ids(
        old_entities,
        new_entities,
        context_word_count=context_word_count,
    )
    ordered = [*old, *new]
    nodes_by_id = {
        str(entity.get("id") or ""): dict(entity)
        for entity in ordered
        if entity.get("id")
    }
    order = list(nodes_by_id)
    order_index = {entity_id: index for index, entity_id in enumerate(order)}
    new_ids = {str(entity.get("id") or "") for entity in new if entity.get("id")}
    adjacency: dict[str, set[str]] = {}
    for source, target in proposals:
        source, target = str(source or ""), str(target or "")
        if (
            source in new_ids
            and source in nodes_by_id
            and target in nodes_by_id
            and source != target
        ):
            adjacency.setdefault(source, set()).add(target)

    components = _strongly_connected_components(order, adjacency)
    cyclic_members = {
        member: component
        for component in components
        if len(component) > 1
        for member in component
    }
    for source in list(adjacency):
        adjacency[source] = {
            target
            for target in adjacency[source]
            if not (
                source in cyclic_members
                and target in cyclic_members[source]
            )
        }
        if not adjacency[source]:
            adjacency.pop(source)

    indegree = {entity_id: 0 for entity_id in order}
    for targets in adjacency.values():
        for target in targets:
            indegree[target] += 1
    ready = deque(entity_id for entity_id in order if indegree[entity_id] == 0)
    member_ids = {entity_id: {entity_id} for entity_id in order}
    visited: list[str] = []
    while ready:
        source = ready.popleft()
        visited.append(source)
        for target in sorted(adjacency.get(source, set()), key=order_index.get):
            member_ids[target].update(member_ids[source])
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if len(visited) != len(order):
        raise ValueError("entity merge graph remained cyclic")

    removed_new_ids = set(adjacency)
    surviving_ids = [
        entity_id for entity_id in order if entity_id not in removed_new_ids
    ]
    entities = [
        _merge_metadata(
            nodes_by_id[entity_id],
            [nodes_by_id[member_id] for member_id in order if member_id in member_ids[entity_id]],
            context_word_count=context_word_count,
        )
        for entity_id in surviving_ids
    ]
    surviving_new = [
        entity for entity in entities if str(entity.get("id") or "") in new_ids
    ]
    return entities, surviving_new


def deduplicate_edges(edges: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in edges:
        normalized = {
            "source": str(edge.get("source") or ""),
            "target": str(edge.get("target") or ""),
            "type": str(edge.get("type") or ""),
        }
        key = (normalized["source"], normalized["target"], normalized["type"])
        if all(key) and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result
