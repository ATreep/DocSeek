from backend.app.api.projects import acquire_lock, is_locked, release_lock
from backend.app.config import get_settings
from backend.app.db import initialize


def test_project_lock_is_exclusive(tmp_path):
    settings = get_settings().model_copy(update={"data_dir": tmp_path})
    settings.ensure_directories()
    initialize(settings.sqlite_path)
    from backend.app.seed import seed_defaults
    seed_defaults(settings)
    from backend.app.db import connect
    with connect(settings.sqlite_path) as db:
        db.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES ('p1','P1','now','now')")
    assert acquire_lock(settings, "p1", "j1")
    assert not acquire_lock(settings, "p1", "j2")
    assert is_locked(settings, "p1")
    release_lock(settings, "p1", "j1")
    assert not is_locked(settings, "p1")
