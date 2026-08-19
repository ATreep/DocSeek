import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS groups (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS roles (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  immutable INTEGER NOT NULL DEFAULT 0,
  capabilities_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_groups (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, group_id)
);
CREATE TABLE IF NOT EXISTS group_roles (
  group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  PRIMARY KEY (group_id, role_id)
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  active_snapshot TEXT,
  candidate_snapshot TEXT,
  error TEXT,
  error_detail TEXT,
  llm_response TEXT,
  routes_json TEXT NOT NULL DEFAULT '{}',
  stage_started_at TEXT,
  stage_detail TEXT,
  timings_json TEXT NOT NULL DEFAULT '{}',
  input_json TEXT NOT NULL DEFAULT '{}',
  progress_json TEXT NOT NULL DEFAULT '{}',
  heartbeat TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_locks (
  project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  job_id TEXT NOT NULL,
  acquired_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS system_config (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_preferences (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  preferences_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_profiles (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  provider_type TEXT NOT NULL,
  model TEXT NOT NULL,
  base_url TEXT,
  secret_configured INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_invocation_logs (
  id TEXT PRIMARY KEY,
  request_time TEXT NOT NULL,
  response_time TEXT NOT NULL,
  duration_ms INTEGER NOT NULL,
  model TEXT NOT NULL,
  route_key TEXT,
  profile_id TEXT,
  status TEXT NOT NULL,
  request_prompt TEXT NOT NULL,
  response_output TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_invocation_logs_request_time
  ON llm_invocation_logs(request_time DESC);
"""


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize(path: Path) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
        if "routes_json" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN routes_json TEXT NOT NULL DEFAULT '{}'")
        if "error_detail" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN error_detail TEXT")
        if "llm_response" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN llm_response TEXT")
        if "stage_started_at" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN stage_started_at TEXT")
        if "stage_detail" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN stage_detail TEXT")
        if "timings_json" not in columns:
            connection.execute(
                "ALTER TABLE jobs ADD COLUMN timings_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "input_json" not in columns:
            connection.execute(
                "ALTER TABLE jobs ADD COLUMN input_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "progress_json" not in columns:
            connection.execute(
                "ALTER TABLE jobs ADD COLUMN progress_json TEXT NOT NULL DEFAULT '{}'"
            )
        # Remove the prototype-only SQLite property table from databases created
        # before the graph/catalog boundary was enforced.
        connection.execute("DROP TABLE IF EXISTS properties")
