from backend.app.services.relation_batches import (
    apply_entity_merges,
    chunk_nodes,
    merge_call_specs,
    relation_call_specs,
)


def _nodes(prefix: str, count: int):
    return [{"id": f"{prefix}{index}"} for index in range(count)]


def _entity(entity_id: str, property_id: str):
    return {
        "id": entity_id,
        "name": entity_id,
        "definition": f"Definition of {entity_id}.",
        "source_property_ids": [property_id],
        "source_contexts": [{"property_id": property_id, "text": entity_id}],
    }


def test_collection_schedules_cover_new_pairs_without_old_old_calls():
    assert chunk_nodes([], 10) == []
    assert [len(group) for group in chunk_nodes(_nodes("n", 1))] == [1]
    assert [len(group) for group in chunk_nodes(_nodes("n", 10))] == [10]
    assert [len(group) for group in chunk_nodes(_nodes("n", 11))] == [10, 1]

    new, old = _nodes("n", 11), _nodes("o", 11)
    within, cross = relation_call_specs(new, old)
    assert [spec.kind for spec in within] == ["within", "within"]
    assert [spec.kind for spec in cross] == [
        "new-new",
        "new-old",
        "new-old",
        "new-old",
        "new-old",
    ]
    assert merge_call_specs(new, old) == [*within, *cross]


def test_entity_merge_graph_propagates_chains_and_retains_unmentioned_nodes():
    old = [_entity("entity-3", "p3")]
    new = [
        _entity("entity-1", "p1"),
        _entity("entity-2", "p2"),
        _entity("unmentioned", "p4"),
    ]

    entities, surviving_new = apply_entity_merges(
        old,
        new,
        [("entity-1", "entity-2"), ("entity-2", "entity-3")],
    )

    assert [entity["id"] for entity in entities] == ["entity-3", "unmentioned"]
    terminal = entities[0]
    assert terminal["source_property_ids"] == ["p3", "p1", "p2"]
    assert {(item["property_id"], item["text"]) for item in terminal["source_contexts"]} == {
        ("p1", "entity-1"),
        ("p2", "entity-2"),
        ("p3", "entity-3"),
    }
    assert [entity["id"] for entity in surviving_new] == ["unmentioned"]


def test_entity_merge_graph_supports_fan_out_and_discards_cycles():
    new = [
        _entity("source", "p1"),
        _entity("left", "p2"),
        _entity("right", "p3"),
        _entity("cycle-a", "p4"),
        _entity("cycle-b", "p5"),
    ]

    entities, _ = apply_entity_merges(
        [],
        new,
        [
            ("source", "left"),
            ("source", "right"),
            ("cycle-a", "cycle-b"),
            ("cycle-b", "cycle-a"),
        ],
    )

    by_id = {entity["id"]: entity for entity in entities}
    assert set(by_id) == {"left", "right", "cycle-a", "cycle-b"}
    assert "p1" in by_id["left"]["source_property_ids"]
    assert "p1" in by_id["right"]["source_property_ids"]
    assert by_id["cycle-a"]["source_property_ids"] == ["p4"]
    assert by_id["cycle-b"]["source_property_ids"] == ["p5"]
