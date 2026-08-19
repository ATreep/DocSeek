from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import monotonic

from backend.app.config import Settings
from backend.app.db import connect, initialize
from backend.app.services.agents import PGBAgent
from backend.app.services.graph_store import GraphRAGBuilder
from backend.app.services.parallelism import load_batch_llm_concurrency
from backend.app.services.providers import ProviderError, chat_provider
from backend.app.services.relation_batches import merge_call_specs, relation_call_specs
from backend.app.services.relation_batches import apply_entity_merges


def _configured_routes(settings: Settings) -> dict[str, str | int | None]:
    with connect(settings.sqlite_path) as db:
        values = {
            row["key"]: row["value"]
            for row in db.execute(
                "SELECT key,value FROM system_config WHERE key IN "
                "('entity_agent_route','pgb_agent_route','batch_llm_concurrency')"
            )
        }
        profiles = {
            row["id"]: dict(row)
            for row in db.execute(
                "SELECT id,model FROM provider_profiles"
            )
        }
    return {
        "entity_route": values.get("entity_agent_route"),
        "property_route": values.get("pgb_agent_route"),
        "concurrency": load_batch_llm_concurrency(settings),
        "entity_model": profiles.get(values.get("entity_agent_route"), {}).get("model"),
        "property_model": profiles.get(values.get("pgb_agent_route"), {}).get("model"),
    }


def _run(
    settings: Settings,
    calls,
    worker,
    concurrency: int,
    *,
    result_sink: list | None = None,
) -> tuple[float, int, str | None]:
    if not calls:
        return 0.0, 0, None
    started = monotonic()
    try:
        with ThreadPoolExecutor(max_workers=min(concurrency, len(calls))) as executor:
            futures = [executor.submit(worker, call) for call in calls]
            for future in as_completed(futures):
                result = future.result()
                if result_sink is not None:
                    result_sink.append(result)
    except ProviderError as exc:
        return monotonic() - started, len(calls), str(exc)
    return monotonic() - started, len(calls), None


def main() -> None:
    settings = Settings(data_dir=Path("data"))
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    routes = _configured_routes(settings)
    concurrency = int(routes["concurrency"] or 1)
    old_entities = [
        {
            "id": "old-entity",
            "name": "Old Entity",
            "definition": "An existing benchmark entity.",
        }
    ]
    new_entities = [
        {
            "id": f"new-entity-{index}",
            "name": f"New Entity {index}",
            "definition": f"A distinct benchmark entity number {index}.",
        }
        for index in range(11)
    ]
    old_properties = [
        {
            "id": "old-property",
            "filename": "old.md",
            "definition": "An existing benchmark property.",
            "property_type": "markdown",
        }
    ]
    new_properties = [
        {
            "id": f"new-property-{index}",
            "filename": f"new-{index}.md",
            "definition": f"A distinct benchmark property number {index}.",
            "property_type": "markdown",
        }
        for index in range(11)
    ]
    merge_calls = merge_call_specs(new_entities, old_entities)
    entity_within, entity_cross = relation_call_specs(new_entities, old_entities)
    property_within, property_cross = relation_call_specs(new_properties, old_properties)

    def entity_builder_call(method: str, pair):
        provider = chat_provider(settings, route_key="entity_agent_route", timeout=settings.entity_agent_timeout_seconds)
        builder = GraphRAGBuilder("benchmark", "benchmark", llm=provider)
        try:
            return getattr(builder, method)(pair)
        finally:
            if provider is not None:
                provider.close()

    def property_call(pair):
        agent = PGBAgent(settings=settings)
        try:
            return agent.propose_pair(pair)
        finally:
            if agent.provider is not None:
                agent.provider.close()

    timings = {}
    merge_results: list = []
    timings["Merging redundant entities"] = _run(
        settings,
        merge_calls,
        lambda pair: entity_builder_call("propose_merges", pair),
        concurrency,
        result_sink=merge_results,
    )
    merge_application_started = monotonic()
    proposals = [proposal for result in merge_results for proposal in result]
    apply_entity_merges(old_entities, new_entities, proposals)
    timings["Apply Redundant Entity Merges"] = [
        monotonic() - merge_application_started,
        len(proposals),
        None,
    ]
    timings["Entity Relation Substage 1"] = _run(
        settings,
        entity_within,
        lambda pair: entity_builder_call("generate_relation_edges", pair),
        concurrency,
    )
    timings["Entity Relation Substage 2"] = _run(
        settings,
        entity_cross,
        lambda pair: entity_builder_call("generate_relation_edges", pair),
        concurrency,
    )
    timings["Property Relation Substage 1"] = _run(
        settings,
        property_within,
        property_call,
        concurrency,
    )
    timings["Property Relation Substage 2"] = _run(
        settings,
        property_cross,
        property_call,
        concurrency,
    )
    print(json.dumps({"config": routes, "timings": timings}, ensure_ascii=False))


if __name__ == "__main__":
    main()
