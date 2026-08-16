import uuid
from datetime import datetime, timezone
from pathlib import Path
import mimetypes
import traceback

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..db import connect
from ..security import get_current_user, require_capability
from ..services.agents import DGAgent, DefinitionResult, GAAgent
from ..services.grouping import apply_group_placements
from ..services.parsers import extract_text, property_type
from ..services.pipeline import _current_group_tree, run_pipeline
from ..services.property_imports import PropertyImportStore
from ..services.storage import move_original, replace_original, safe_filename, safe_directory, save_original
from ..services.catalog import PropertyCatalog
from .projects import get_project, is_locked, acquire_lock, release_lock

router = APIRouter(prefix="/projects", tags=["properties"])


class PropertyUpdate(BaseModel):
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    definition: str | None = Field(default=None, max_length=4000)


class MoveRequest(BaseModel):
    directory: str = Field(default="", max_length=255)


class PropertyImportConfirm(BaseModel):
    filename: str = Field(min_length=1, max_length=255)


class RegroupRequest(BaseModel):
    revision_prompt: str = Field(min_length=1, max_length=4000)


def _schedule(background_tasks: BackgroundTasks | None, function, *args):
    if background_tasks is None:
        function(*args)
    else:
        background_tasks.add_task(function, *args)


def generate_property_metadata(
    settings: Settings,
    filename: str,
    kind: str,
    path: Path,
    comment: str,
) -> DefinitionResult:
    return DGAgent(settings=settings).generate(filename, kind, extract_text(path, kind), comment)


def _enqueue_property(settings: Settings, project_id: str, filename: str, content: bytes, content_type: str | None, comment: str = "", background_tasks: BackgroundTasks | None = None) -> dict:
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    filename, path = save_original(settings, project_id, filename, content)
    kind = property_type(filename, content_type)
    property_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    if not acquire_lock(settings, project_id, job_id):
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="Project is processing")
    try:
        PropertyCatalog(settings).create(project_id, {"id": property_id, "project_id": project_id, "filename": filename, "property_type": kind, "relative_path": f"properties/{filename}", "definition": None, "status": "queued", "created_at": now, "updated_at": now})
        with connect(settings.sqlite_path) as db:
            db.execute("INSERT INTO jobs(id,project_id,stage,status,candidate_snapshot,heartbeat) VALUES (?,?,?,?,?,?)", (job_id, project_id, "queued", "queued", None, now))
    except Exception:
        release_lock(settings, project_id, job_id)
        path.unlink(missing_ok=True)
        raise
    try:
        with connect(settings.sqlite_path) as db:
            db.execute(
                "UPDATE jobs SET stage='dg-agent', status='running', heartbeat=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), job_id),
            )
        metadata = generate_property_metadata(settings, filename, kind, path, comment)
    except Exception as exc:
        failed_at = datetime.now(timezone.utc).isoformat()
        with connect(settings.sqlite_path) as db:
            db.execute(
                "UPDATE jobs SET stage='failed', status='failed', error=?, error_detail=?, heartbeat=? WHERE id=?",
                (str(exc), traceback.format_exc(), failed_at, job_id),
            )
        PropertyCatalog(settings).update(
            project_id,
            property_id,
            {"status": "failed", "updated_at": failed_at},
        )
        release_lock(settings, project_id, job_id)
        raise
    _schedule(background_tasks, run_pipeline, settings, project_id, property_id, job_id, filename, kind, path, comment, "add", metadata.definition)
    return {"property_id": property_id, "job_id": job_id, "status": "queued", "property_type": kind, "suggested_filename": metadata.filename_suggestion}


def _stage_property_import(
    settings: Settings,
    project_id: str,
    filename: str,
    content: bytes,
    content_type: str | None,
    comment: str = "",
) -> dict:
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    import_id = str(uuid.uuid4())
    store = PropertyImportStore(settings)
    clean_filename, path = store.stage(project_id, import_id, filename, content)
    kind = property_type(clean_filename, content_type)
    try:
        metadata = generate_property_metadata(settings, clean_filename, kind, path, comment)
        suggested_filename = safe_filename(metadata.filename_suggestion or clean_filename)
        store.save(
            project_id,
            import_id,
            {
                "id": import_id,
                "project_id": project_id,
                "original_filename": clean_filename,
                "source_filename": path.name,
                "content_type": content_type,
                "property_type": kind,
                "definition": metadata.definition,
                "comment": comment,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        store.discard(project_id, import_id)
        raise
    return {
        "import_id": import_id,
        "status": "awaiting_confirmation",
        "property_type": kind,
        "original_filename": clean_filename,
        "suggested_filename": suggested_filename,
        "definition": metadata.definition,
    }


def _confirm_property_import(
    settings: Settings,
    project_id: str,
    import_id: str,
    filename: str,
    background_tasks: BackgroundTasks | None = None,
) -> dict:
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    store = PropertyImportStore(settings)
    try:
        staged = store.get(project_id, import_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Property import not found") from exc
    if not staged:
        raise HTTPException(status_code=404, detail="Property import not found")

    clean_filename = safe_filename(filename)
    property_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    if not acquire_lock(settings, project_id, job_id):
        raise HTTPException(status_code=409, detail="Project is processing")

    path: Path | None = None
    catalog = PropertyCatalog(settings)
    catalog_created = False
    now = datetime.now(timezone.utc).isoformat()
    try:
        clean_filename, path = save_original(
            settings,
            project_id,
            clean_filename,
            staged["source_path"].read_bytes(),
        )
        kind = property_type(clean_filename, staged.get("content_type"))
        catalog.create(
            project_id,
            {
                "id": property_id,
                "project_id": project_id,
                "filename": clean_filename,
                "property_type": kind,
                "relative_path": f"properties/{clean_filename}",
                "definition": None,
                "status": "queued",
                "created_at": now,
                "updated_at": now,
            },
        )
        catalog_created = True
        with connect(settings.sqlite_path) as db:
            db.execute(
                "INSERT INTO jobs(id,project_id,stage,status,candidate_snapshot,heartbeat) VALUES (?,?,?,?,?,?)",
                (job_id, project_id, "queued", "queued", None, now),
            )
    except Exception:
        release_lock(settings, project_id, job_id)
        if catalog_created:
            catalog.delete(project_id, property_id)
        if path is not None:
            path.unlink(missing_ok=True)
        raise

    store.discard(project_id, import_id)
    _schedule(
        background_tasks,
        run_pipeline,
        settings,
        project_id,
        property_id,
        job_id,
        clean_filename,
        kind,
        path,
        staged.get("comment", ""),
        "add",
        staged.get("definition", ""),
    )
    return {
        "property_id": property_id,
        "job_id": job_id,
        "status": "queued",
        "property_type": kind,
    }


def _enqueue_replacement(settings: Settings, project_id: str, property_id: str, filename: str, content: bytes, content_type: str | None, background_tasks: BackgroundTasks | None = None) -> dict:
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    catalog = PropertyCatalog(settings)
    row = catalog.get(project_id, property_id)
    if not row:
        raise HTTPException(status_code=404, detail="Property not found")
    job_id = str(uuid.uuid4())
    if not acquire_lock(settings, project_id, job_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    path = settings.projects_dir / project_id / row["relative_path"]
    clean_filename = safe_filename(filename)
    kind = property_type(clean_filename, content_type)
    now = datetime.now(timezone.utc).isoformat()
    try:
        replace_original(path, content)
        catalog.update(project_id, property_id, {"filename": clean_filename, "property_type": kind, "status": "queued", "updated_at": now})
        with connect(settings.sqlite_path) as db:
            db.execute("INSERT INTO jobs(id,project_id,stage,status,candidate_snapshot,heartbeat) VALUES (?,?,?,?,?,?)", (job_id, project_id, "queued", "queued", None, now))
    except Exception:
        release_lock(settings, project_id, job_id)
        raise
    _schedule(background_tasks, run_pipeline, settings, project_id, property_id, job_id, clean_filename, kind, path, "", "replace")
    return {"property_id": property_id, "job_id": job_id, "status": "queued"}


def _enqueue_removal(settings: Settings, project_id: str, property_id: str, background_tasks: BackgroundTasks | None = None) -> dict:
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    catalog = PropertyCatalog(settings)
    row = catalog.get(project_id, property_id)
    if not row:
        raise HTTPException(status_code=404, detail="Property not found")
    job_id = str(uuid.uuid4())
    if not acquire_lock(settings, project_id, job_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    now = datetime.now(timezone.utc).isoformat()
    catalog.update(project_id, property_id, {"status": "removing", "updated_at": now})
    with connect(settings.sqlite_path) as db:
        db.execute("INSERT INTO jobs(id,project_id,stage,status,candidate_snapshot,heartbeat) VALUES (?,?,?,?,?,?)", (job_id, project_id, "queued", "queued", None, now))
    path = settings.projects_dir / project_id / row["relative_path"]
    _schedule(background_tasks, run_pipeline, settings, project_id, property_id, job_id, row["filename"], row["property_type"], path, "", "remove")
    return {"property_id": property_id, "job_id": job_id, "status": "removing"}


def _row(row):
    return dict(row) if row else None


@router.get("/{project_id}/properties")
def list_properties(project_id: str, settings: Settings = Depends(get_settings), user=Depends(require_capability("property.view"))):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return sorted(PropertyCatalog(settings).list(project_id), key=lambda item: item.get("filename", ""))


@router.post("/{project_id}/properties", status_code=202)
def add_property(
    project_id: str,
    file: UploadFile = File(...),
    comment: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("property.upload")),
):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return _stage_property_import(
        settings,
        project_id,
        file.filename or "property",
        file.file.read(),
        file.content_type,
        comment,
    )


@router.post(
    "/{project_id}/property-imports/{import_id}/confirm",
    status_code=202,
)
def confirm_property_import(
    project_id: str,
    import_id: str,
    payload: PropertyImportConfirm,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("property.upload")),
):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return _confirm_property_import(
        settings,
        project_id,
        import_id,
        payload.filename,
        background_tasks,
    )


@router.delete(
    "/{project_id}/property-imports/{import_id}",
    status_code=204,
)
def cancel_property_import(
    project_id: str,
    import_id: str,
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("property.upload")),
):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    store = PropertyImportStore(settings)
    try:
        staged = store.get(project_id, import_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Property import not found") from exc
    if not staged:
        raise HTTPException(status_code=404, detail="Property import not found")
    store.discard(project_id, import_id)
    return Response(status_code=204)


@router.post("/{project_id}/properties/regroup")
def regroup_properties(
    project_id: str,
    payload: RegroupRequest,
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("property.move")),
):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    catalog = PropertyCatalog(settings)
    rows = catalog.list(project_id)
    if not rows:
        return {"properties": []}
    revision_prompt = payload.revision_prompt.strip()
    if not revision_prompt:
        raise HTTPException(status_code=422, detail="Re-grouping prompt is required")
    job_id = str(uuid.uuid4())
    if not acquire_lock(settings, project_id, job_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    now = datetime.now(timezone.utc).isoformat()
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO jobs(id,project_id,stage,status,candidate_snapshot,heartbeat,stage_started_at,stage_detail) VALUES (?,?,?,?,?,?,?,?)",
            (job_id, project_id, "ga-agent", "running", None, now, now, "Rearranging property tree"),
        )
    try:
        tree_context = _current_group_tree(rows)
        placements = GAAgent(settings=settings).rearrange_tree(
            tree_context,
            revision_prompt,
        )
        updated = apply_group_placements(
            settings,
            project_id,
            rows,
            placements,
        )
        completed_at = datetime.now(timezone.utc).isoformat()
        with connect(settings.sqlite_path) as db:
            db.execute(
                "UPDATE jobs SET stage='active',status='completed',heartbeat=?,stage_started_at=?,stage_detail='Property tree rearranged' WHERE id=?",
                (completed_at, completed_at, job_id),
            )
        return {
            "properties": sorted(updated, key=lambda item: str(item.get("filename") or "").casefold()),
            "job_id": job_id,
        }
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        failed_at = datetime.now(timezone.utc).isoformat()
        with connect(settings.sqlite_path) as db:
            db.execute(
                "UPDATE jobs SET stage='failed',status='failed',error=?,error_detail=?,heartbeat=?,stage_started_at=?,stage_detail=? WHERE id=?",
                (str(exc), traceback.format_exc(), failed_at, failed_at, str(exc), job_id),
            )
        if isinstance(exc, FileNotFoundError):
            status_code = 404
        elif isinstance(exc, FileExistsError):
            status_code = 409
        else:
            status_code = 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except Exception as exc:
        failed_at = datetime.now(timezone.utc).isoformat()
        with connect(settings.sqlite_path) as db:
            db.execute(
                "UPDATE jobs SET stage='failed',status='failed',error=?,error_detail=?,heartbeat=?,stage_started_at=?,stage_detail=? WHERE id=?",
                (str(exc), traceback.format_exc(), failed_at, failed_at, str(exc), job_id),
            )
        raise HTTPException(status_code=502, detail=f"Re-grouping failed: {exc}") from exc
    finally:
        release_lock(settings, project_id, job_id)


@router.get("/{project_id}/properties/{property_id}")
def get_property(project_id: str, property_id: str, settings: Settings = Depends(get_settings), user=Depends(require_capability("property.view"))):
    row = PropertyCatalog(settings).get(project_id, property_id)
    if not row:
        raise HTTPException(status_code=404, detail="Property not found")
    return row


@router.get("/{project_id}/properties/{property_id}/attribute")
def get_property_attribute(project_id: str, property_id: str, settings: Settings = Depends(get_settings), user=Depends(require_capability("property.attribute.view"))):
    row = PropertyCatalog(settings).get(project_id, property_id)
    if not row:
        raise HTTPException(status_code=404, detail="Property not found")
    return {"property_id": property_id, "definition": row["definition"], "property_type": row["property_type"], "status": row["status"]}


@router.patch("/{project_id}/properties/{property_id}")
def update_property(project_id: str, property_id: str, payload: PropertyUpdate, background_tasks: BackgroundTasks, settings: Settings = Depends(get_settings), user=Depends(get_current_user)):
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    catalog = PropertyCatalog(settings)
    row = catalog.get(project_id, property_id)
    if not row:
        raise HTTPException(status_code=404, detail="Property not found")
    changes: dict[str, str] = {}
    new_path = settings.projects_dir / project_id / row["relative_path"]
    if payload.filename is not None:
        if "property.rename" not in user["capabilities"]:
            raise HTTPException(status_code=403, detail="Missing capability: property.rename")
        clean_filename = safe_filename(payload.filename)
        if clean_filename != row["filename"]:
            relative = Path(row["relative_path"])
            directory = str(relative.parent.relative_to(Path("properties"))) if relative.parts[:1] == ("properties",) and relative.parent != Path("properties") else ""
            try:
                moved_relative, new_path = move_original(settings, project_id, row["relative_path"], directory, clean_filename)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="Original property file not found") from exc
            except FileExistsError as exc:
                raise HTTPException(status_code=409, detail=f"Target property already exists: {exc}") from exc
            changes["relative_path"] = moved_relative
        changes["filename"] = clean_filename
    if payload.definition is not None:
        if "property.attribute.edit" not in user["capabilities"]:
            raise HTTPException(status_code=403, detail="Missing capability: property.attribute.edit")
        changes["definition"] = payload.definition.strip()
    if not changes:
        raise HTTPException(status_code=422, detail="Provide filename or definition")
    changes["status"] = "queued"
    changes["updated_at"] = datetime.now(timezone.utc).isoformat()
    job_id = str(uuid.uuid4())
    if not acquire_lock(settings, project_id, job_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    try:
        updated = catalog.update(project_id, property_id, changes)
        now = datetime.now(timezone.utc).isoformat()
        with connect(settings.sqlite_path) as db:
            db.execute("INSERT INTO jobs(id,project_id,stage,status,candidate_snapshot,heartbeat) VALUES (?,?,?,?,?,?)", (job_id, project_id, "queued", "queued", None, now))
    except Exception:
        release_lock(settings, project_id, job_id)
        raise
    _schedule(background_tasks, run_pipeline, settings, project_id, property_id, job_id, updated["filename"], updated["property_type"], new_path, "", "metadata", updated.get("definition") or row.get("definition") or "")
    return updated


@router.put("/{project_id}/properties/{property_id}/content", status_code=202)
def replace_property(
    project_id: str,
    property_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("property.replace")),
):
    return _enqueue_replacement(settings, project_id, property_id, file.filename or "property", file.file.read(), file.content_type, background_tasks)


@router.post("/{project_id}/properties/{property_id}/move")
def move_property(project_id: str, property_id: str, payload: MoveRequest, settings: Settings = Depends(get_settings), user=Depends(require_capability("property.move"))):
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    catalog = PropertyCatalog(settings)
    row = catalog.get(project_id, property_id)
    if not row:
        raise HTTPException(status_code=404, detail="Property not found")
    try:
        relative_path, _ = move_original(settings, project_id, row["relative_path"], payload.directory, row["filename"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Original property file not found") from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=f"Target property already exists: {exc}") from exc
    return catalog.update(project_id, property_id, {"relative_path": relative_path, "directory": payload.directory.strip("/"), "updated_at": datetime.now(timezone.utc).isoformat()})


@router.get("/{project_id}/properties/{property_id}/raw")
def raw_property(project_id: str, property_id: str, settings: Settings = Depends(get_settings), user=Depends(require_capability("property.view"))):
    row = PropertyCatalog(settings).get(project_id, property_id)
    if not row:
        raise HTTPException(status_code=404, detail="Property not found")
    path = settings.projects_dir / project_id / row["relative_path"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Original property file not found")
    return FileResponse(path, media_type=mimetypes.guess_type(row["filename"])[0] or "application/octet-stream", filename=row["filename"])


@router.delete("/{project_id}/properties/{property_id}", status_code=202)
def remove_property(project_id: str, property_id: str, background_tasks: BackgroundTasks, settings: Settings = Depends(get_settings), user=Depends(require_capability("property.delete"))):
    return _enqueue_removal(settings, project_id, property_id, background_tasks)
