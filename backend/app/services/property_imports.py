from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..config import Settings
from .storage import safe_filename


class PropertyImportStore:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _import_dir(self, project_id: str, import_id: str) -> Path:
        if not import_id or Path(import_id).name != import_id:
            raise ValueError("Invalid property import id")
        return self.settings.projects_dir / project_id / "imports" / import_id

    def stage(
        self,
        project_id: str,
        import_id: str,
        filename: str,
        content: bytes,
    ) -> tuple[str, Path]:
        clean_filename = safe_filename(filename)
        import_dir = self._import_dir(project_id, import_id)
        import_dir.mkdir(parents=True, exist_ok=False)
        source_path = import_dir / f"source{Path(clean_filename).suffix}"
        source_path.write_bytes(content)
        return clean_filename, source_path

    def save(self, project_id: str, import_id: str, record: dict[str, Any]) -> None:
        import_dir = self._import_dir(project_id, import_id)
        if not import_dir.is_dir():
            raise FileNotFoundError(import_dir)
        target = import_dir / "import.json"
        fd, temporary = tempfile.mkstemp(prefix="property-import-", suffix=".json", dir=import_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, indent=2)
                stream.write("\n")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def get(self, project_id: str, import_id: str) -> dict[str, Any] | None:
        import_dir = self._import_dir(project_id, import_id)
        path = import_dir / "import.json"
        if not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(record, dict):
            return None
        source_path = import_dir / str(record.get("source_filename", ""))
        if not source_path.is_file() or source_path.parent != import_dir:
            return None
        return {**record, "source_path": source_path}

    def discard(self, project_id: str, import_id: str) -> None:
        import_dir = self._import_dir(project_id, import_id)
        if import_dir.is_dir():
            shutil.rmtree(import_dir)
