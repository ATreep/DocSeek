import uuid
import base64
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timezone
from pathlib import Path
import json
import mimetypes
import shutil
import tempfile
import traceback
import inspect
from typing import BinaryIO, Iterator

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..db import connect
from ..security import get_current_user, require_capability
from ..services.agents import (
    DGAgent,
    DefinitionResult,
    GAAgent,
    readable_property_identifier,
    unique_readable_property_identifier,
)
from ..services.extraction_text import (
    DEFAULT_EXTRACTION_TEXT_MAX_CHARS,
    ExtractionChunk,
    ExtractionSelection,
    TemporaryExtractionStore,
)
from ..services.grouping import apply_group_placements, catalog_signature
from ..services.model_errors import extract_model_response
from ..services.parallelism import load_batch_llm_concurrency
from ..services.parsers import extract_text, property_type
from ..services.pipeline import (
    _current_group_tree,
    run_batch_pipeline,
    run_pipeline,
    run_property_removal,
    run_property_removals,
)
from ..services.property_imports import PropertyImportStore
from ..services.text_metrics import property_content_metrics
from ..services.storage import delete_property_text, move_original, read_property_text, replace_original, safe_directory, safe_filename, save_original, write_property_text
from ..services.catalog import PropertyCatalog
from ..services.display_language import (
    current_display_language,
    iterate_in_display_language,
    run_in_display_language,
)
from .projects import get_project, is_locked, acquire_lock, release_lock

router = APIRouter(prefix="/projects", tags=["properties"])

PROPERTY_IMPORT_KEEPALIVE_SECONDS = 10.0


def _batch_llm_workers(settings: Settings, total: int) -> int:
    return max(1, min(total, load_batch_llm_concurrency(settings)))


def _direct_extraction_selection(content: str) -> ExtractionSelection:
    """Bound property text without tokenization, scoring, or chunk ranking."""
    source = str(content or "")
    selected_text = source[:DEFAULT_EXTRACTION_TEXT_MAX_CHARS].rstrip()
    chunks = (
        [
            ExtractionChunk(
                start=0,
                end=len(selected_text),
                text=selected_text,
                section="Document",
            )
        ]
        if selected_text
        else []
    )
    return ExtractionSelection(
        text=selected_text,
        chunks=chunks,
        original_character_count=len(source),
        selected_character_count=len(selected_text),
    )


class PropertyUpdate(BaseModel):
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    definition: str | None = Field(default=None, max_length=4000)


class MoveRequest(BaseModel):
    directory: str = Field(default="", max_length=255)


class PropertyImportConfirm(BaseModel):
    filename: str = Field(min_length=1, max_length=255)


class PropertyImportBatchItemConfirm(BaseModel):
    import_id: str = Field(min_length=1, max_length=255)
    filename: str = Field(min_length=1, max_length=255)


class PropertyImportBatchConfirm(BaseModel):
    items: list[PropertyImportBatchItemConfirm] = Field(min_length=1)


class RegroupRequest(BaseModel):
    revision_prompt: str = Field(min_length=1, max_length=4000)


class RegroupConfirmationItem(BaseModel):
    property_id: str = Field(min_length=1, max_length=255)
    directory: str = Field(default="", max_length=255)
    filename: str = Field(min_length=1, max_length=255)


class RegroupConfirmation(BaseModel):
    catalog_signature: str = Field(min_length=64, max_length=64)
    items: list[RegroupConfirmationItem] = Field(min_length=1)


class PropertyBatchDelete(BaseModel):
    property_ids: list[str] = Field(min_length=1, max_length=1000)


def _schedule(background_tasks: BackgroundTasks | None, function, *args):
    language = current_display_language()
    if background_tasks is None:
        run_in_display_language(language, function, *args)
    else:
        background_tasks.add_task(run_in_display_language, language, function, *args)


def generate_property_metadata(
    settings: Settings,
    filename: str,
    kind: str,
    path: Path,
    comment: str,
    *,
    full_text: str | None = None,
    extraction_text: str | None = None,
    existing_entities: list[dict] | None = None,
) -> DefinitionResult:
    text = full_text if full_text is not None else extract_text(path, kind)
    if extraction_text is None and kind != "image":
        extraction_text = _direct_extraction_selection(text).text
    image_data_url = None
    if kind == "image":
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        image_data_url = (
            f"data:{media_type};base64,"
            f"{base64.b64encode(path.read_bytes()).decode('ascii')}"
        )
    return DGAgent(settings=settings).generate(
        filename,
        kind,
        text,
        comment,
        image_data_url=image_data_url,
        extraction_text=extraction_text,
    )


def _generate_metadata_compatibly(
    settings: Settings,
    filename: str,
    kind: str,
    path: Path,
    comment: str,
    *,
    full_text: str,
    extraction_text: str,
    existing_entities: list[dict],
) -> DefinitionResult:
    """Allow tests and integrations with the previous five-argument seam."""
    parameters = inspect.signature(generate_property_metadata).parameters
    if "extraction_text" not in parameters:
        return generate_property_metadata(settings, filename, kind, path, comment)
    return generate_property_metadata(
        settings,
        filename,
        kind,
        path,
        comment,
        full_text=full_text,
        extraction_text=extraction_text,
        existing_entities=existing_entities,
    )


def _enqueue_property(settings: Settings, project_id: str, filename: str, content: bytes, content_type: str | None, comment: str = "", background_tasks: BackgroundTasks | None = None) -> dict:
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    filename, path = save_original(settings, project_id, filename, content)
    kind = property_type(filename, content_type)
    catalog = PropertyCatalog(settings)
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    if not acquire_lock(settings, project_id, job_id):
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="Project is processing")
    try:
        with connect(settings.sqlite_path) as db:
            db.execute("INSERT INTO jobs(id,project_id,stage,status,candidate_snapshot,heartbeat) VALUES (?,?,?,?,?,?)", (job_id, project_id, "dg-agent", "running", None, now))
    except Exception:
        release_lock(settings, project_id, job_id)
        path.unlink(missing_ok=True)
        raise
    property_id = ""
    catalog_created = False
    extraction_path: Path | None = None
    try:
        full_text = extract_text(path, kind)
        prompt_selection = _direct_extraction_selection(full_text)
        metadata = _generate_metadata_compatibly(
            settings,
            filename,
            kind,
            path,
            comment,
            full_text=full_text,
            extraction_text=prompt_selection.text,
            existing_entities=[],
        )
        used_property_ids = {
            str(row.get("id") or "").casefold()
            for row in catalog.list(project_id)
        }
        property_id = unique_readable_property_identifier(
            metadata.property_id
            or readable_property_identifier(filename, metadata.definition),
            used_property_ids,
        )
        catalog.create(project_id, {"id": property_id, "project_id": project_id, "filename": filename, "property_type": kind, "relative_path": f"properties/{filename}", "definition": metadata.definition, "status": "queued", "created_at": now, "updated_at": now})
        catalog_created = True
        canonical_content = metadata.content or (
            full_text if kind != "image" else metadata.definition
        )
        write_property_text(settings, project_id, property_id, canonical_content)
        extraction_path = TemporaryExtractionStore(settings).save(
            project_id,
            job_id,
            property_id,
            _direct_extraction_selection(canonical_content),
        )
    except Exception as exc:
        failed_at = datetime.now(timezone.utc).isoformat()
        with connect(settings.sqlite_path) as db:
            db.execute(
                "UPDATE jobs SET stage='failed', status='failed', error=?, error_detail=?, llm_response=?, heartbeat=? WHERE id=?",
                (
                    str(exc),
                    traceback.format_exc(),
                    extract_model_response(exc),
                    failed_at,
                    job_id,
                ),
            )
        if catalog_created:
            PropertyCatalog(settings).update(
                project_id,
                property_id,
                {"status": "failed", "updated_at": failed_at},
            )
            delete_property_text(settings, project_id, property_id)
        else:
            path.unlink(missing_ok=True)
        TemporaryExtractionStore(settings).delete(extraction_path)
        release_lock(settings, project_id, job_id)
        raise
    _schedule(
        background_tasks,
        run_pipeline,
        settings,
        project_id,
        property_id,
        job_id,
        filename,
        kind,
        path,
        comment,
        "add",
        metadata.definition,
        str(extraction_path) if extraction_path else "",
    )
    return {"property_id": property_id, "job_id": job_id, "status": "queued", "property_type": kind, "suggested_filename": filename}


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
        full_text = extract_text(path, kind)
        prompt_selection = _direct_extraction_selection(full_text)
        metadata = _generate_metadata_compatibly(
            settings,
            clean_filename,
            kind,
            path,
            comment,
            full_text=full_text,
            extraction_text=prompt_selection.text,
            existing_entities=[],
        )
        canonical_content = metadata.content or (
            full_text if kind != "image" else metadata.definition
        )
        store.save_extraction(
            project_id,
            import_id,
            _direct_extraction_selection(canonical_content),
        )
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
                "property_id": metadata.property_id,
                "comment": comment,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        store.save_content(project_id, import_id, canonical_content)
    except Exception:
        store.discard(project_id, import_id)
        raise
    return {
        "import_id": import_id,
        "status": "awaiting_confirmation",
        "property_type": kind,
        "original_filename": clean_filename,
        "definition": metadata.definition,
        "property_id": metadata.property_id,
        **property_content_metrics(canonical_content),
    }


def _add_import_plan(
    settings: Settings,
    project_id: str,
    items: list[dict],
    comment: str,
) -> list[dict]:
    rows = PropertyCatalog(settings).list(project_id)
    tree_context = _current_group_tree(rows) if rows else {}
    used_property_ids = {
        str(row.get("id") or "").casefold() for row in rows
    }
    store = PropertyImportStore(settings)
    planned_items: list[dict] = []
    for item in items:
        property_id = unique_readable_property_identifier(
            item.get("property_id")
            or readable_property_identifier(
                item.get("original_filename"),
                item.get("definition"),
            ),
            used_property_ids,
        )
        planned_items.append({**item, "property_id": property_id})
        store.update(
            project_id,
            item["import_id"],
            {"property_id": property_id},
        )

    proposal = GAAgent(settings=settings).plan_import(
        tree_context,
        planned_items,
        comment,
    )
    used_paths = {
        (
            str(
                row.get("directory")
                or (
                    Path(str(row.get("relative_path") or "")).parent.relative_to(
                        Path("properties")
                    ).as_posix()
                    if Path(str(row.get("relative_path") or "")).parent
                    != Path("properties")
                    else ""
                )
            ).casefold(),
            str(row.get("filename") or "").casefold(),
        )
        for row in rows
    }
    result: list[dict] = []
    for item in planned_items:
        property_id = item["property_id"]
        directory_path = safe_directory(proposal.directories.get(property_id, ""))
        suggested_directory = (
            "" if directory_path == Path() else directory_path.as_posix()
        )
        suggested_filename = safe_filename(
            proposal.filenames.get(property_id) or item["original_filename"]
        )
        candidate_path = (suggested_directory.casefold(), suggested_filename.casefold())
        if candidate_path in used_paths:
            filename_path = Path(suggested_filename)
            index = 2
            while candidate_path in used_paths:
                suggested_filename = (
                    f"{filename_path.stem}-{index}{filename_path.suffix}"
                )
                candidate_path = (
                    suggested_directory.casefold(),
                    suggested_filename.casefold(),
                )
                index += 1
        used_paths.add(candidate_path)
        store.update(
            project_id,
            item["import_id"],
            {"suggested_directory": suggested_directory},
        )
        result.append({
            **item,
            "suggested_filename": suggested_filename,
            "suggested_directory": suggested_directory,
        })
    return result


def _stage_property_import_batch(
    settings: Settings,
    project_id: str,
    files: list[UploadFile],
    comment: str = "",
) -> dict:
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    if not files:
        raise HTTPException(status_code=422, detail="Select at least one property")

    batch_id = str(uuid.uuid4())
    language = current_display_language()
    store = PropertyImportStore(settings)
    items: list[dict] = []
    try:
        staged_by_index: dict[int, dict] = {}
        first_error: Exception | None = None
        with ThreadPoolExecutor(
            max_workers=_batch_llm_workers(settings, len(files)),
            thread_name_prefix="property-import",
        ) as executor:
            futures = {
                executor.submit(
                    run_in_display_language,
                    language,
                    _stage_property_import,
                    settings,
                    project_id,
                    file.filename or "property",
                    file.file.read(),
                    file.content_type,
                    comment,
                ): index
                for index, file in enumerate(files)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    staged_by_index[index] = future.result()
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
        items = [staged_by_index[index] for index in sorted(staged_by_index)]
        if first_error is not None:
            raise first_error
        items = _add_import_plan(settings, project_id, items, comment)
        store.save_batch(
            project_id,
            batch_id,
            [item["import_id"] for item in items],
        )
    except Exception:
        for item in items:
            store.discard(project_id, item["import_id"])
        raise
    return {
        "batch_id": batch_id,
        "status": "awaiting_confirmation",
        "items": items,
    }


def _stream_property_import_batch(
    settings: Settings,
    project_id: str,
    files: list[tuple[str, BinaryIO, str | None]],
    comment: str = "",
) -> Iterator[str]:
    batch_id = str(uuid.uuid4())
    language = current_display_language()
    store = PropertyImportStore(settings)
    items: list[dict] = []
    total = len(files)
    worker_count = _batch_llm_workers(settings, total)

    def event(payload: dict) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"

    try:
        yield event(
            {
                "type": "batch_started",
                "batch_id": batch_id,
                "total": total,
                "workers": worker_count,
            }
        )
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="property-import",
        ) as executor:
            pending: dict = {}
            for index, (filename, source, content_type) in enumerate(
                files, start=1
            ):
                yield event(
                    {
                        "type": "file_started",
                        "batch_id": batch_id,
                        "index": index,
                        "total": total,
                        "filename": filename,
                    }
                )
                future = executor.submit(
                    run_in_display_language,
                    language,
                    _stage_property_import,
                    settings,
                    project_id,
                    filename,
                    source.read(),
                    content_type,
                    comment,
                )
                pending[future] = (index, filename)

            staged_by_index: dict[int, dict] = {}
            first_error: Exception | None = None
            while pending:
                completed, _ = wait(
                    tuple(pending),
                    timeout=PROPERTY_IMPORT_KEEPALIVE_SECONDS,
                    return_when=FIRST_COMPLETED,
                )
                if not completed:
                    index, filename = min(pending.values())
                    yield event(
                        {
                            "type": "keepalive",
                            "batch_id": batch_id,
                            "index": index,
                            "total": total,
                            "filename": filename,
                        }
                    )
                    continue
                for future in sorted(
                    completed, key=lambda current: pending[current][0]
                ):
                    index, filename = pending.pop(future)
                    try:
                        item = future.result()
                    except Exception as exc:
                        if first_error is None:
                            first_error = exc
                        continue
                    staged_by_index[index] = item
                    yield event(
                        {
                            "type": "file_analyzed",
                            "batch_id": batch_id,
                            "index": index,
                            "total": total,
                            "filename": filename,
                            "item": item,
                        }
                    )
            items = [staged_by_index[index] for index in sorted(staged_by_index)]
            if first_error is not None:
                raise first_error
            yield event(
                {
                    "type": "import_plan_generation_started",
                    "batch_id": batch_id,
                    "total": total,
                }
            )
            future = executor.submit(
                run_in_display_language,
                language,
                _add_import_plan,
                settings,
                project_id,
                items,
                comment,
            )
            while True:
                completed, _ = wait(
                    (future,), timeout=PROPERTY_IMPORT_KEEPALIVE_SECONDS
                )
                if not completed:
                    yield event(
                        {
                            "type": "import_plan_generation_keepalive",
                            "batch_id": batch_id,
                            "total": total,
                        }
                    )
                    continue
                items = future.result()
                break
        store.save_batch(
            project_id,
            batch_id,
            [item["import_id"] for item in items],
        )
        yield event(
            {
                "type": "batch_completed",
                "batch_id": batch_id,
                "status": "awaiting_confirmation",
                "total": total,
                "items": items,
            }
        )
    except Exception as exc:
        for item in items:
            store.discard(project_id, item["import_id"])
        yield event({"type": "error", "batch_id": batch_id, "message": str(exc)})
    finally:
        for _, source, _ in files:
            source.close()


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
    catalog = PropertyCatalog(settings)
    used_property_ids = {
        str(row.get("id") or "").casefold() for row in catalog.list(project_id)
    }
    property_id = unique_readable_property_identifier(
        staged.get("property_id")
        or readable_property_identifier(
            clean_filename,
            staged.get("definition"),
            staged.get("original_filename"),
        ),
        used_property_ids,
    )
    job_id = str(uuid.uuid4())
    if not acquire_lock(settings, project_id, job_id):
        raise HTTPException(status_code=409, detail="Project is processing")

    path: Path | None = None
    extraction_path: Path | None = None
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
        write_property_text(
            settings, project_id, property_id, staged.get("content", "")
        )
        with connect(settings.sqlite_path) as db:
            db.execute(
                "INSERT INTO jobs(id,project_id,stage,status,candidate_snapshot,heartbeat) VALUES (?,?,?,?,?,?)",
                (job_id, project_id, "queued", "queued", None, now),
            )
        if isinstance(staged.get("extraction"), dict):
            extraction_path = TemporaryExtractionStore(settings).save(
                project_id,
                job_id,
                property_id,
                staged["extraction"],
            )
    except Exception:
        release_lock(settings, project_id, job_id)
        if catalog_created:
            catalog.delete(project_id, property_id)
        if path is not None:
            path.unlink(missing_ok=True)
        delete_property_text(settings, project_id, property_id)
        TemporaryExtractionStore(settings).delete(extraction_path)
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
        str(extraction_path) if extraction_path else "",
    )
    return {
        "property_id": property_id,
        "job_id": job_id,
        "status": "queued",
        "property_type": kind,
    }


def _confirm_property_import_batch(
    settings: Settings,
    project_id: str,
    batch_id: str,
    confirmed_items: list[PropertyImportBatchItemConfirm],
    background_tasks: BackgroundTasks | None = None,
) -> dict:
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    store = PropertyImportStore(settings)
    try:
        batch = store.get_batch(project_id, batch_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail="Property import batch not found"
        ) from exc
    if not batch:
        raise HTTPException(status_code=404, detail="Property import batch not found")

    confirmed_by_id = {item.import_id: item for item in confirmed_items}
    if (
        len(confirmed_by_id) != len(confirmed_items)
        or set(confirmed_by_id) != set(batch["import_ids"])
    ):
        raise HTTPException(
            status_code=422,
            detail="Confirm every staged property exactly once",
        )
    clean_filenames = [
        safe_filename(confirmed_by_id[import_id].filename)
        for import_id in batch["import_ids"]
    ]
    planned_directories = [
        (
            ""
            if (directory_path := safe_directory(staged.get("suggested_directory", "")))
            == Path()
            else directory_path.as_posix()
        )
        for staged in batch["imports"]
    ]
    if len({filename.casefold() for filename in clean_filenames}) != len(
        clean_filenames
    ):
        raise HTTPException(
            status_code=422,
            detail="Property filenames in one import must be unique",
        )

    catalog = PropertyCatalog(settings)
    existing_target_paths = {
        str(
            settings.projects_dir
            / project_id
            / str(row.get("relative_path") or "")
        ).casefold()
        for row in catalog.list(project_id)
    }
    planned_target_paths = [
        str(
            settings.projects_dir
            / project_id
            / "properties"
            / safe_directory(directory)
            / filename
        ).casefold()
        for directory, filename in zip(planned_directories, clean_filenames)
    ]
    if (
        len(set(planned_target_paths)) != len(planned_target_paths)
        or any(path in existing_target_paths for path in planned_target_paths)
    ):
        raise HTTPException(
            status_code=422,
            detail="Confirmed property filenames conflict with the planned property tree",
        )
    used_property_ids = {
        str(row.get("id") or "").casefold() for row in catalog.list(project_id)
    }
    property_ids = [
        unique_readable_property_identifier(
            staged.get("property_id")
            or readable_property_identifier(
                filename,
                staged.get("definition"),
                staged.get("original_filename"),
            ),
            used_property_ids,
        )
        for staged, filename in zip(batch["imports"], clean_filenames)
    ]
    planned_directories_by_id = dict(zip(property_ids, planned_directories))
    job_id = str(uuid.uuid4())
    if not acquire_lock(settings, project_id, job_id):
        raise HTTPException(status_code=409, detail="Project is processing")

    created_property_ids: list[str] = []
    created_paths: list[Path] = []
    extraction_paths: list[Path] = []
    pipeline_items: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    try:
        for property_id, staged, requested_filename, directory in zip(
            property_ids, batch["imports"], clean_filenames, planned_directories
        ):
            stored_filename, path = save_original(
                settings,
                project_id,
                requested_filename,
                staged["source_path"].read_bytes(),
            )
            created_paths.append(path)
            relative_path, path = move_original(
                settings,
                project_id,
                f"properties/{stored_filename}",
                directory,
                requested_filename,
            )
            created_paths[-1] = path
            clean_filename = requested_filename
            kind = property_type(clean_filename, staged.get("content_type"))
            catalog.create(
                project_id,
                {
                    "id": property_id,
                    "project_id": project_id,
                    "filename": clean_filename,
                    "property_type": kind,
                    "relative_path": relative_path,
                    "directory": directory,
                    "definition": staged.get("definition", ""),
                    "status": "queued",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            created_property_ids.append(property_id)
            write_property_text(
                settings, project_id, property_id, staged.get("content", "")
            )
            extraction_path = None
            if isinstance(staged.get("extraction"), dict):
                extraction_path = TemporaryExtractionStore(settings).save(
                    project_id,
                    job_id,
                    property_id,
                    staged["extraction"],
                )
                extraction_paths.append(extraction_path)
            pipeline_items.append(
                {
                    "property_id": property_id,
                    "filename": clean_filename,
                    "kind": kind,
                    "path": path,
                    "comment": staged.get("comment", ""),
                    "definition": staged.get("definition", ""),
                    "text": staged.get("content", ""),
                    "extraction_path": str(extraction_path) if extraction_path else "",
                    "directory": directory,
                }
            )
        with connect(settings.sqlite_path) as db:
            db.execute(
                "INSERT INTO jobs(id,project_id,stage,status,candidate_snapshot,heartbeat,input_json,progress_json) VALUES (?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    project_id,
                    "queued",
                    "queued",
                    None,
                    now,
                    json.dumps(
                        {
                            "operation": "batch-add",
                            "items": [
                                {
                                    **item,
                                    "path": str(item["path"]),
                                    "text": None,
                                }
                                for item in pipeline_items
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {"directories": planned_directories_by_id},
                        ensure_ascii=False,
                    ),
                ),
            )
    except Exception:
        with connect(settings.sqlite_path) as db:
            db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        for property_id in created_property_ids:
            catalog.delete(project_id, property_id)
            delete_property_text(settings, project_id, property_id)
        for path in created_paths:
            path.unlink(missing_ok=True)
        extraction_store = TemporaryExtractionStore(settings)
        for extraction_path in extraction_paths:
            extraction_store.delete(extraction_path)
        release_lock(settings, project_id, job_id)
        raise

    store.discard_batch(project_id, batch_id)
    _schedule(
        background_tasks,
        run_batch_pipeline,
        settings,
        project_id,
        job_id,
        pipeline_items,
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "properties": [
            {
                "property_id": item["property_id"],
                "job_id": job_id,
                "status": "queued",
                "property_type": item["kind"],
                "filename": item["filename"],
            }
            for item in pipeline_items
        ],
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
    _schedule(
        background_tasks,
        run_property_removal,
        settings,
        project_id,
        property_id,
        job_id,
        path,
    )
    return {"property_id": property_id, "job_id": job_id, "status": "removing"}


def _enqueue_batch_removal(
    settings: Settings,
    project_id: str,
    property_ids: list[str],
    background_tasks: BackgroundTasks | None = None,
) -> dict:
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    unique_property_ids = list(dict.fromkeys(str(item).strip() for item in property_ids))
    if not all(unique_property_ids):
        raise HTTPException(status_code=422, detail="Property identifiers cannot be empty")
    catalog = PropertyCatalog(settings)
    rows_by_id = {
        str(row.get("id") or ""): row for row in catalog.list(project_id)
    }
    missing = [
        property_id
        for property_id in unique_property_ids
        if property_id not in rows_by_id
    ]
    if missing:
        raise HTTPException(status_code=404, detail="One or more properties were not found")
    job_id = str(uuid.uuid4())
    if not acquire_lock(settings, project_id, job_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    now = datetime.now(timezone.utc).isoformat()
    for property_id in unique_property_ids:
        catalog.update(
            project_id,
            property_id,
            {"status": "removing", "updated_at": now},
        )
    with connect(settings.sqlite_path) as db:
        db.execute(
            "INSERT INTO jobs(id,project_id,stage,status,candidate_snapshot,heartbeat,input_json) VALUES (?,?,?,?,?,?,?)",
            (
                job_id,
                project_id,
                "queued",
                "queued",
                None,
                now,
                json.dumps(
                    {"operation": "batch-delete", "property_ids": unique_property_ids},
                    separators=(",", ":"),
                ),
            ),
        )
    paths = [
        settings.projects_dir / project_id / rows_by_id[property_id]["relative_path"]
        for property_id in unique_property_ids
    ]
    _schedule(
        background_tasks,
        run_property_removals,
        settings,
        project_id,
        unique_property_ids,
        job_id,
        paths,
    )
    return {
        "property_ids": unique_property_ids,
        "job_id": job_id,
        "status": "removing",
    }


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
    item = _stage_property_import(
        settings,
        project_id,
        file.filename or "property",
        file.file.read(),
        file.content_type,
        comment,
    )
    try:
        return _add_import_plan(settings, project_id, [item], comment)[0]
    except Exception:
        PropertyImportStore(settings).discard(project_id, item["import_id"])
        raise


@router.post("/{project_id}/property-import-batches", status_code=202)
def add_property_batch(
    project_id: str,
    files: list[UploadFile] = File(...),
    comment: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("property.upload")),
):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return _stage_property_import_batch(
        settings,
        project_id,
        files,
        comment,
    )


@router.post("/{project_id}/property-import-batches/stream")
def stream_property_batch(
    project_id: str,
    files: list[UploadFile] = File(...),
    comment: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("property.upload")),
):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    if not files:
        raise HTTPException(status_code=422, detail="Select at least one property")

    prepared_files: list[tuple[str, BinaryIO, str | None]] = []
    try:
        for file in files:
            source = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
            shutil.copyfileobj(file.file, source)
            source.seek(0)
            prepared_files.append(
                (file.filename or "property", source, file.content_type)
            )
    except Exception:
        for _, source, _ in prepared_files:
            source.close()
        raise

    events = _stream_property_import_batch(
        settings,
        project_id,
        prepared_files,
        comment,
    )
    return StreamingResponse(
        iterate_in_display_language(current_display_language(), events),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store"},
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


@router.post(
    "/{project_id}/property-import-batches/{batch_id}/confirm",
    status_code=202,
)
def confirm_property_import_batch(
    project_id: str,
    batch_id: str,
    payload: PropertyImportBatchConfirm,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("property.upload")),
):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return _confirm_property_import_batch(
        settings,
        project_id,
        batch_id,
        payload.items,
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


@router.delete(
    "/{project_id}/property-import-batches/{batch_id}",
    status_code=204,
)
def cancel_property_import_batch(
    project_id: str,
    batch_id: str,
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("property.upload")),
):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    store = PropertyImportStore(settings)
    try:
        staged = store.get_batch(project_id, batch_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail="Property import batch not found"
        ) from exc
    if not staged:
        raise HTTPException(status_code=404, detail="Property import batch not found")
    store.discard_batch(project_id, batch_id)
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
        return {"catalog_signature": catalog_signature([]), "changes": []}
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
        proposal = GAAgent(settings=settings).propose_tree(
            tree_context,
            revision_prompt,
        )
        completed_at = datetime.now(timezone.utc).isoformat()
        with connect(settings.sqlite_path) as db:
            db.execute(
                "UPDATE jobs SET stage='proposal-ready',status='completed',heartbeat=?,stage_started_at=?,stage_detail='Property tree proposal ready' WHERE id=?",
                (completed_at, completed_at, job_id),
            )
        changes = []
        for row in rows:
            property_id = str(row.get("id") or "")
            current_directory = _property_directory(row)
            proposed_directory = proposal.directories.get(
                property_id, current_directory
            )
            current_filename = str(row.get("filename") or "property")
            proposed_filename = safe_filename(
                proposal.filenames.get(property_id, current_filename)
            )
            changes.append(
                {
                    "property_id": property_id,
                    "current_directory": current_directory,
                    "proposed_directory": proposed_directory,
                    "current_filename": current_filename,
                    "proposed_filename": proposed_filename,
                    "definition": row.get("definition") or "",
                    "changed": current_directory != proposed_directory
                    or current_filename != proposed_filename,
                }
            )
        return {
            "catalog_signature": catalog_signature(rows),
            "changes": sorted(
                changes,
                key=lambda item: str(item.get("current_filename") or "").casefold(),
            ),
            "job_id": job_id,
        }
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        failed_at = datetime.now(timezone.utc).isoformat()
        with connect(settings.sqlite_path) as db:
            db.execute(
                "UPDATE jobs SET stage='failed',status='failed',error=?,error_detail=?,llm_response=?,heartbeat=?,stage_started_at=?,stage_detail=? WHERE id=?",
                (
                    str(exc),
                    traceback.format_exc(),
                    extract_model_response(exc),
                    failed_at,
                    failed_at,
                    str(exc),
                    job_id,
                ),
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
                "UPDATE jobs SET stage='failed',status='failed',error=?,error_detail=?,llm_response=?,heartbeat=?,stage_started_at=?,stage_detail=? WHERE id=?",
                (
                    str(exc),
                    traceback.format_exc(),
                    extract_model_response(exc),
                    failed_at,
                    failed_at,
                    str(exc),
                    job_id,
                ),
            )
        raise HTTPException(status_code=502, detail=f"Re-grouping failed: {exc}") from exc
    finally:
        release_lock(settings, project_id, job_id)


def _property_directory(row: dict) -> str:
    directory = str(row.get("directory") or "").strip("/")
    if directory:
        return directory
    relative_path = Path(str(row.get("relative_path") or ""))
    if relative_path.parts[:1] != ("properties",) or relative_path.parent == Path("properties"):
        return ""
    return relative_path.parent.relative_to(Path("properties")).as_posix()


@router.post("/{project_id}/properties/regroup/confirm")
def confirm_regroup_properties(
    project_id: str,
    payload: RegroupConfirmation,
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("property.move")),
):
    if not get_project(settings, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    if is_locked(settings, project_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    catalog = PropertyCatalog(settings)
    rows = catalog.list(project_id)
    if catalog_signature(rows) != payload.catalog_signature:
        raise HTTPException(
            status_code=409,
            detail="The property tree changed after this proposal was generated. Create a new proposal.",
        )
    row_ids = {str(row.get("id") or "") for row in rows}
    submitted_ids = {item.property_id for item in payload.items}
    if submitted_ids != row_ids or len(payload.items) != len(row_ids):
        raise HTTPException(
            status_code=422,
            detail="The confirmation must include every property exactly once.",
        )
    placements = {item.property_id: item.directory for item in payload.items}
    filenames = {
        item.property_id: safe_filename(item.filename) for item in payload.items
    }
    job_id = str(uuid.uuid4())
    if not acquire_lock(settings, project_id, job_id):
        raise HTTPException(status_code=409, detail="Project is processing")
    try:
        updated = apply_group_placements(
            settings,
            project_id,
            rows,
            placements,
            filenames,
        )
        return {
            "properties": sorted(
                updated,
                key=lambda item: str(item.get("filename") or "").casefold(),
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Original property file not found") from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=f"Target property already exists: {exc}") from exc
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


@router.get("/{project_id}/properties/{property_id}/content", response_class=PlainTextResponse)
def property_content(
    project_id: str,
    property_id: str,
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("property.view")),
):
    row = PropertyCatalog(settings).get(project_id, property_id)
    if not row:
        raise HTTPException(status_code=404, detail="Property not found")
    content = read_property_text(settings, project_id, property_id)
    if content is None:
        path = settings.projects_dir / project_id / row["relative_path"]
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Original property file not found")
        content = extract_text(path, row["property_type"])
        if row["property_type"] == "image" and not content:
            content = row.get("definition") or ""
        write_property_text(settings, project_id, property_id, content)
    return PlainTextResponse(content, media_type="text/plain; charset=utf-8")


@router.post("/{project_id}/properties/batch-delete", status_code=202)
def remove_properties(
    project_id: str,
    payload: PropertyBatchDelete,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    user=Depends(require_capability("property.delete")),
):
    return _enqueue_batch_removal(
        settings,
        project_id,
        payload.property_ids,
        background_tasks,
    )


@router.delete("/{project_id}/properties/{property_id}", status_code=202)
def remove_property(project_id: str, property_id: str, background_tasks: BackgroundTasks, settings: Settings = Depends(get_settings), user=Depends(require_capability("property.delete"))):
    return _enqueue_removal(settings, project_id, property_id, background_tasks)
