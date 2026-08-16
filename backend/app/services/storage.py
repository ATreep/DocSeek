import re
from pathlib import Path

from ..config import Settings


def safe_filename(filename: str) -> str:
    name = Path(filename or "property").name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name or "property"


def property_dir(settings: Settings, project_id: str) -> Path:
    path = settings.projects_dir / project_id / "properties"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_directory(directory: str) -> Path:
    normalized = (directory or "").strip().strip("/")
    if not normalized or normalized == ".":
        return Path()
    parts = Path(normalized).parts
    if any(
        part in {"", ".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9_-]+(?: [A-Za-z0-9_-]+)*", part)
        for part in parts
    ):
        raise ValueError("Directory may contain only safe folder names")
    return Path(*parts)


def save_original(settings: Settings, project_id: str, filename: str, content: bytes) -> tuple[str, Path]:
    clean = safe_filename(filename)
    target = property_dir(settings, project_id) / clean
    stem, suffix = target.stem, target.suffix
    counter = 1
    while target.exists():
        target = target.with_name(f"{stem}-{counter}{suffix}")
        counter += 1
    target.write_bytes(content)
    return target.name, target


def replace_original(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def move_original(settings: Settings, project_id: str, relative_path: str, directory: str, filename: str) -> tuple[str, Path]:
    source = settings.projects_dir / project_id / relative_path
    if not source.exists():
        raise FileNotFoundError(source)
    target_dir = property_dir(settings, project_id) / safe_directory(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_filename(filename)
    if target != source and target.exists():
        raise FileExistsError(target)
    source.replace(target)
    return str(target.relative_to(settings.projects_dir / project_id)), target
