from datetime import datetime, timedelta, timezone

from ..config import Settings
from ..db import connect


def recover_stale_jobs(settings: Settings, stale_after_seconds: int = 900) -> list[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
    now = datetime.now(timezone.utc).isoformat()
    recovered: list[str] = []
    with connect(settings.sqlite_path) as db:
        rows = db.execute(
            "SELECT j.id,j.project_id FROM jobs j JOIN project_locks l ON l.job_id=j.id WHERE j.status IN ('queued','running') AND j.heartbeat<?",
            (cutoff.isoformat(),),
        ).fetchall()
        for row in rows:
            db.execute("UPDATE jobs SET status='failed', stage='recovery', error=?, heartbeat=? WHERE id=?", ("Recovered stale processing job after restart", now, row["id"]))
            db.execute("DELETE FROM project_locks WHERE project_id=? AND job_id=?", (row["project_id"], row["id"]))
            recovered.append(row["id"])
    return recovered
