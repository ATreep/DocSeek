from __future__ import annotations

import shutil
import tempfile
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import Settings
from .catalog import PropertyCatalog
from .storage import property_dir, safe_directory, safe_filename


def _stored_directory(row: dict) -> str:
    directory = str(row.get("directory") or "").strip("/")
    if directory:
        return directory
    relative_path = Path(str(row.get("relative_path") or ""))
    if relative_path.parts[:1] != ("properties",) or relative_path.parent == Path("properties"):
        return ""
    return relative_path.parent.relative_to(Path("properties")).as_posix()


def apply_group_placements(
    settings: Settings,
    project_id: str,
    catalog_rows: list[dict],
    placements: dict[str, str],
    filenames: dict[str, str] | None = None,
) -> list[dict]:
    project_root = settings.projects_dir / project_id
    properties_root = property_dir(settings, project_id)
    updated_at = datetime.now(timezone.utc).isoformat()
    updated_rows: list[dict] = []
    moves: list[dict[str, Path]] = []
    target_paths: dict[str, str] = {}

    for row in catalog_rows:
        property_id = str(row.get("id") or "")
        requested_directory = placements.get(property_id, _stored_directory(row))
        directory_path = safe_directory(requested_directory)
        directory = "" if directory_path == Path() else directory_path.as_posix()
        filename = safe_filename(
            str((filenames or {}).get(property_id) or row.get("filename") or "property")
        )
        source = project_root / str(row.get("relative_path") or "")
        target = properties_root / directory_path / filename
        target_key = str(target).casefold()
        if target_key in target_paths and target_paths[target_key] != property_id:
            raise ValueError(f"Re-grouping would create duplicate property path: {directory}/{filename}".strip("/"))
        target_paths[target_key] = property_id
        updated_rows.append({
            **row,
            "filename": filename,
            "directory": directory,
            "relative_path": str(target.relative_to(project_root)),
            "updated_at": updated_at,
        })
        if source != target:
            moves.append({"source": source, "target": target})

    moving_sources = {str(move["source"]).casefold() for move in moves}
    for move in moves:
        source, target = move["source"], move["target"]
        if not source.is_file():
            raise FileNotFoundError(source)
        if target.exists() and str(target).casefold() not in moving_sources:
            raise FileExistsError(target)

    jobs_dir = project_root / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix="property-regroup-", dir=jobs_dir))
    staged_moves: list[dict[str, Path]] = []
    try:
        for index, move in enumerate(moves):
            staged = staging_root / f"{index}-{move['source'].name}"
            move["source"].replace(staged)
            staged_moves.append({**move, "staged": staged})
        for move in staged_moves:
            move["target"].parent.mkdir(parents=True, exist_ok=True)
            move["staged"].replace(move["target"])
        return PropertyCatalog(settings).replace_all(project_id, updated_rows)
    except Exception:
        for move in reversed(staged_moves):
            current = move["target"] if move["target"].exists() else move["staged"]
            if current.exists():
                move["source"].parent.mkdir(parents=True, exist_ok=True)
                current.replace(move["source"])
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def catalog_signature(catalog_rows: list[dict]) -> str:
    payload = [
        {
            "id": str(row.get("id") or ""),
            "filename": str(row.get("filename") or ""),
            "directory": _stored_directory(row),
            "relative_path": str(row.get("relative_path") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        }
        for row in sorted(catalog_rows, key=lambda item: str(item.get("id") or ""))
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
