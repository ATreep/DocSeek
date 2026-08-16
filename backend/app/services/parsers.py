from functools import lru_cache
from pathlib import Path

from markitdown import MarkItDown

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}
DOCUMENT_EXTENSIONS = {".docx"}
SPREADSHEET_EXTENSIONS = {".xls", ".xlsx", ".xlsm"}
PRESENTATION_EXTENSIONS = {".pptx"}
PLAIN_TEXT_EXTENSIONS = {
    "",
    ".bash",
    ".c",
    ".cfg",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".fish",
    ".go",
    ".h",
    ".hpp",
    ".htm",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsonl",
    ".jsx",
    ".kt",
    ".kts",
    ".log",
    ".markdown",
    ".md",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsv",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}


class PropertyConversionError(RuntimeError):
    """Raised when an uploaded document cannot be converted to Markdown."""


@lru_cache(maxsize=1)
def _document_converter() -> MarkItDown:
    return MarkItDown()


def property_type(filename: str, content_type: str | None = None) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_EXTENSIONS or (content_type or "").startswith("image/"):
        return "image"
    if suffix in DOCUMENT_EXTENSIONS:
        return "document"
    if suffix in SPREADSHEET_EXTENSIONS:
        return "spreadsheet"
    if suffix in PRESENTATION_EXTENSIONS:
        return "presentation"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix in {".py", ".js", ".ts", ".tsx", ".java", ".go", ".rs", ".css", ".html"}:
        return "code"
    if suffix == ".pdf":
        return "pdf"
    return "text"


def extract_text(path: Path, kind: str) -> str:
    if kind == "image":
        return ""
    if path.suffix.lower() not in PLAIN_TEXT_EXTENSIONS:
        try:
            return _document_converter().convert_local(path).markdown.strip()
        except Exception as exc:
            raise PropertyConversionError(
                f"Unable to extract content from {path.name}"
            ) from exc
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""
