import re
import unicodedata
from pathlib import Path

from ..config import Settings


def safe_filename(filename: str) -> str:
    normalized = unicodedata.normalize("NFC", filename or "property")
    name = Path(normalized.replace("\\", "/")).name
    name = "".join(
        "_"
        if character in '<>:"|?*' or unicodedata.category(character).startswith("C")
        else character
        for character in name
    )
    name = re.sub(r"_+", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or "property"


def property_dir(settings: Settings, project_id: str) -> Path:
    path = settings.projects_dir / project_id / "properties"
    path.mkdir(parents=True, exist_ok=True)
    return path


def extracted_text_dir(settings: Settings, project_id: str) -> Path:
    path = settings.projects_dir / project_id / "extracted-text"
    path.mkdir(parents=True, exist_ok=True)
    return path


def property_text_path(settings: Settings, project_id: str, property_id: str) -> Path:
    if not property_id or Path(property_id).name != property_id:
        raise ValueError("Invalid property id")
    return extracted_text_dir(settings, project_id) / f"{property_id}.txt"


def write_property_text(
    settings: Settings, project_id: str, property_id: str, content: str
) -> Path:
    target = property_text_path(settings, project_id, property_id)
    target.write_text(str(content or "").strip(), encoding="utf-8")
    return target


def read_property_text(
    settings: Settings, project_id: str, property_id: str
) -> str | None:
    path = property_text_path(settings, project_id, property_id)
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def delete_property_text(settings: Settings, project_id: str, property_id: str) -> None:
    property_text_path(settings, project_id, property_id).unlink(missing_ok=True)


def safe_directory(directory: str) -> Path:
    normalized = (directory or "").strip().strip("/")
    if not normalized or normalized == ".":
        return Path()
    parts = Path(normalized).parts
    if any(
        part in {"", ".", ".."}
        or not re.fullmatch(r"[\w-]+(?: [\w-]+)*", part)
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
