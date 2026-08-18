from backend.app.config import Settings
from backend.app.services.extraction_text import (
    ExtractionSelection,
    TemporaryExtractionStore,
    select_extraction_text,
)
from backend.app.services.property_imports import PropertyImportStore


def test_selection_preserves_offsets_and_restores_source_order():
    text = (
        "DOCSEEK PORTAL\n"
        "Page 1 of 4\n\n"
        "# Architecture\n"
        "Atlas uses Neo4j for relation-aware retrieval.\n\n"
        "# Governance\n"
        "The Atlas service depends on the Data Protection Act.\n\n"
        "DOCSEEK PORTAL\n"
        "Page 2 of 4\n"
    )

    result = select_extraction_text(
        text,
        filename="atlas-architecture.md",
        definition="Atlas architecture and governance notes.",
        max_chars=220,
    )

    assert "Atlas uses Neo4j" in result.text
    assert "Data Protection Act" in result.text
    assert "Page 1 of 4" not in result.text
    assert [chunk.start for chunk in result.chunks] == sorted(
        chunk.start for chunk in result.chunks
    )
    assert all(text[chunk.start : chunk.end] == chunk.text for chunk in result.chunks)


def test_selection_hard_includes_existing_entity_mentions():
    text = (
        "# Overview\n"
        "Atlas uses Neo4j and depends on LangGraph for orchestration.\n\n"
        "# Archive\n"
        "Legacy Meridian appears in the migration appendix.\n\n"
        "# Repeated detail\n"
        + ("Atlas uses Neo4j for graph retrieval.\n\n" * 12)
    )

    result = select_extraction_text(
        text,
        filename="atlas.md",
        existing_entities=[
            {
                "id": "meridian",
                "name": "Meridian Platform",
                "aliases": ["Legacy Meridian"],
                "definition": "A retired migration platform.",
            }
        ],
        max_chars=180,
    )

    meridian_chunk = next(
        chunk for chunk in result.chunks if "Legacy Meridian" in chunk.text
    )
    assert meridian_chunk.hard_included is True
    assert "Legacy Meridian" in result.text


def test_selection_uses_diversity_to_keep_independent_sections():
    repeated = "Alice developed Atlas using Neo4j for product deployment."
    text = (
        "# Team\n"
        + "\n\n".join([repeated] * 8)
        + "\n\n# Legal\n"
        "The Atlas license is governed by the Digital Services Act.\n\n"
        "# Operations\n"
        "Northwind owns the Atlas support service in Singapore.\n"
    )

    result = select_extraction_text(
        text,
        filename="atlas-overview.md",
        definition="Atlas product, ownership, and legal overview.",
        max_chars=220,
    )

    assert result.text.count(repeated) <= 2
    assert "Digital Services Act" in result.text
    assert "Northwind owns" in result.text
    assert result.selected_character_count <= 220


def test_import_store_round_trips_temporary_extraction_and_discards_it(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    store = PropertyImportStore(settings)
    store.stage("project", "import", "atlas.md", b"Atlas uses Neo4j.")
    store.save(
        "project",
        "import",
        {
            "id": "import",
            "project_id": "project",
            "source_filename": "source.md",
        },
    )
    selection = ExtractionSelection.from_dict(
        select_extraction_text(
            "Atlas uses Neo4j.", filename="atlas.md", max_chars=100
        ).to_dict()
    )

    extraction_path = store.save_extraction("project", "import", selection)
    staged = store.get("project", "import")

    assert extraction_path.is_file()
    assert staged is not None
    assert staged["extraction"]["text"] == "Atlas uses Neo4j."
    assert staged["extraction"]["chunks"][0]["start"] == 0

    store.discard("project", "import")

    assert not extraction_path.exists()


def test_job_temporary_extraction_survives_transfer_until_explicit_cleanup(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_directories()
    selection = select_extraction_text(
        "Atlas uses Neo4j.", filename="atlas.md", max_chars=100
    )
    store = TemporaryExtractionStore(settings)

    path = store.save("project", "job", "property", selection)

    assert path.is_file()
    assert store.load(path).text == "Atlas uses Neo4j."

    store.delete(path)

    assert not path.exists()
    assert not path.parent.exists()
