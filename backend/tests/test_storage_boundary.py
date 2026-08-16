from backend.app.config import get_settings
from backend.app.db import connect, initialize
from backend.app.services.catalog import PropertyCatalog


def test_sqlite_does_not_contain_canonical_property_records(tmp_path):
    settings = get_settings().model_copy(update={"data_dir": tmp_path})
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    with connect(settings.sqlite_path) as db:
        row = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='properties'").fetchone()
    assert row is None


def test_property_catalog_persists_metadata_outside_sqlite(tmp_path):
    settings = get_settings().model_copy(update={"data_dir": tmp_path})
    settings.ensure_directories()
    catalog = PropertyCatalog(settings)
    catalog.create("project-1", {"id": "property-1", "filename": "notes.md", "status": "queued"})
    assert catalog.get("project-1", "property-1")["filename"] == "notes.md"
    assert not list(settings.conf_dir.glob("*.sqlite3"))
