from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOCSEEK_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    api_port: int = 8000
    frontend_port: int = 5173
    data_dir: Path = Path("./data")
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "docseek"
    neo4j_property_database: str = "property_graph"
    neo4j_entity_database: str = "entity_graph"
    allow_local_fallback: bool = True
    use_neo4j: bool = False
    session_ttl_hours: int = 24
    job_stale_after_seconds: int = 900
    entity_agent_timeout_seconds: float = 300.0
    batch_llm_concurrency: int = 50
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    embedding_model: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None

    @property
    def conf_dir(self) -> Path:
        return self.data_dir / "conf"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def sqlite_path(self) -> Path:
        return self.conf_dir / "docseek.sqlite3"

    def ensure_directories(self) -> None:
        self.conf_dir.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
