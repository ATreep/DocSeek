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

    def _batch_path(self, project_id: str, batch_id: str) -> Path:
        if not batch_id or Path(batch_id).name != batch_id:
            raise ValueError("Invalid property import batch id")
        return (
            self.settings.projects_dir
            / project_id
            / "imports"
            / "batches"
            / f"{batch_id}.json"
        )

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

    def update(
        self,
        project_id: str,
        import_id: str,
        changes: dict[str, Any],
    ) -> None:
        path = self._import_dir(project_id, import_id) / "import.json"
        if not path.is_file():
            raise FileNotFoundError(path)
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise ValueError("Invalid property import record")
        self.save(project_id, import_id, {**record, **changes})

    def save_content(
        self, project_id: str, import_id: str, content: str
    ) -> Path:
        import_dir = self._import_dir(project_id, import_id)
        if not import_dir.is_dir():
            raise FileNotFoundError(import_dir)
        target = import_dir / "content.txt"
        target.write_text(str(content or "").strip(), encoding="utf-8")
        return target

    def save_extraction(
        self, project_id: str, import_id: str, selection: Any
    ) -> Path:
        import_dir = self._import_dir(project_id, import_id)
        if not import_dir.is_dir():
            raise FileNotFoundError(import_dir)
        payload = selection.to_dict() if hasattr(selection, "to_dict") else selection
        if not isinstance(payload, dict):
            raise TypeError("Temporary extraction selection must be an object")
        target = import_dir / "extraction.json"
        fd, temporary = tempfile.mkstemp(
            prefix="property-extraction-",
            suffix=".json",
            dir=import_dir,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target

    def save_batch(
        self, project_id: str, batch_id: str, import_ids: list[str]
    ) -> None:
        target = self._batch_path(project_id, batch_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix="property-import-batch-",
            suffix=".json",
            dir=target.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"id": batch_id, "import_ids": import_ids}, stream, indent=2)
                stream.write("\n")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def get_batch(self, project_id: str, batch_id: str) -> dict[str, Any] | None:
        path = self._batch_path(project_id, batch_id)
        if not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        import_ids = record.get("import_ids") if isinstance(record, dict) else None
        if (
            not isinstance(import_ids, list)
            or not import_ids
            or not all(isinstance(import_id, str) for import_id in import_ids)
            or len(set(import_ids)) != len(import_ids)
        ):
            return None
        imports = [self.get(project_id, import_id) for import_id in import_ids]
        if any(item is None for item in imports):
            return None
        return {
            "id": batch_id,
            "import_ids": import_ids,
            "imports": imports,
        }

    def discard_batch(self, project_id: str, batch_id: str) -> None:
        batch = self.get_batch(project_id, batch_id)
        if batch:
            for import_id in batch["import_ids"]:
                self.discard(project_id, import_id)
        self._batch_path(project_id, batch_id).unlink(missing_ok=True)

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
        content_path = import_dir / "content.txt"
        try:
            content = content_path.read_text(encoding="utf-8") if content_path.is_file() else ""
        except OSError:
            content = ""
        extraction_path = import_dir / "extraction.json"
        extraction = None
        if extraction_path.is_file():
            try:
                parsed = json.loads(extraction_path.read_text(encoding="utf-8"))
                extraction = parsed if isinstance(parsed, dict) else None
            except (OSError, json.JSONDecodeError):
                extraction = None
        return {
            **record,
            "source_path": source_path,
            "content": content,
            "extraction": extraction,
        }

    def discard(self, project_id: str, import_id: str) -> None:
        import_dir = self._import_dir(project_id, import_id)
        if import_dir.is_dir():
            shutil.rmtree(import_dir)
