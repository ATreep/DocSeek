from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..config import Settings


class PropertyCatalog:
    """Local development property catalog; production adapters can map this contract to Neo4j."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def _path(self, project_id: str) -> Path:
        path = self.settings.projects_dir / project_id / "jobs" / "property-catalog.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def list(self, project_id: str) -> list[dict[str, Any]]:
        path = self._path(project_id)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        records = [self._sanitize_record(item) for item in payload if isinstance(item, dict)]
        if records != payload:
            self._write(project_id, records)
        return records

    def get(self, project_id: str, property_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list(project_id) if item.get("id") == property_id), None)

    def create(self, project_id: str, record: dict[str, Any]) -> dict[str, Any]:
        records = self.list(project_id)
        if any(item.get("id") == record.get("id") for item in records):
            raise ValueError(f"Property already exists: {record.get('id')}")
        clean_record = self._sanitize_record(record)
        records.append(clean_record)
        self._write(project_id, records)
        return dict(clean_record)

    def update(self, project_id: str, property_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        records = self.list(project_id)
        for index, item in enumerate(records):
            if item.get("id") == property_id:
                updated = self._sanitize_record({**item, **changes})
                records[index] = updated
                self._write(project_id, records)
                return updated
        raise KeyError(property_id)

    def delete(self, project_id: str, property_id: str) -> None:
        records = [item for item in self.list(project_id) if item.get("id") != property_id]
        self._write(project_id, records)

    def replace_all(self, project_id: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        clean_records = [self._sanitize_record(record) for record in records]
        self._write(project_id, clean_records)
        return [dict(record) for record in clean_records]

    def _write(self, project_id: str, records: list[dict[str, Any]]) -> None:
        path = self._path(project_id)
        records = [self._sanitize_record(record) for record in records]
        fd, temporary = tempfile.mkstemp(prefix="property-catalog-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(records, stream, indent=2)
                stream.write("\n")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _sanitize_record(record: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in record.items() if key != "filename_suggestion"}
