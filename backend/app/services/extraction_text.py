from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

import jieba

from ..config import Settings


DEFAULT_EXTRACTION_TEXT_MAX_CHARS = 24_000
_CHUNK_TARGET_CHARS = 1_200
_MMR_REDUNDANCY_WEIGHT = 0.35
_SECTION_BUDGET_SHARE = 0.45

_RELATION_CUES = (
    "uses",
    "using",
    "depends on",
    "developed by",
    "built by",
    "built with",
    "owned by",
    "owns",
    "maintains",
    "integrates with",
    "connects to",
    "powered by",
    "requires",
    "implements",
    "complies with",
    "governed by",
    "使用",
    "依赖",
    "开发",
    "拥有",
    "维护",
    "集成",
    "连接",
    "遵循",
    "基于",
)
_IMPORTANT_SECTION_WORDS = {
    "abstract",
    "architecture",
    "background",
    "compliance",
    "conclusion",
    "design",
    "governance",
    "introduction",
    "legal",
    "operations",
    "overview",
    "people",
    "product",
    "requirements",
    "results",
    "security",
    "team",
    "摘要",
    "架构",
    "背景",
    "合规",
    "结论",
    "设计",
    "治理",
    "介绍",
    "法律",
    "运营",
    "概述",
    "人员",
    "产品",
    "要求",
    "结果",
    "安全",
    "团队",
}
_LOW_VALUE_SECTION_WORDS = {
    "appendix",
    "changelog",
    "contents",
    "copyright",
    "footer",
    "index",
    "license",
    "navigation",
    "references",
    "附录",
    "目录",
    "版权",
    "索引",
    "参考",
}
_NAVIGATION_WORDS = {
    "back",
    "contents",
    "home",
    "menu",
    "next",
    "previous",
    "skip to content",
    "table of contents",
    "top",
    "返回",
    "目录",
    "首页",
    "上一页",
    "下一页",
}
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
    "以及",
    "一个",
    "这个",
    "用于",
}
_PROFESSIONAL_SUFFIXES = (
    "api",
    "agent",
    "algorithm",
    "architecture",
    "company",
    "database",
    "framework",
    "graph",
    "law",
    "model",
    "organization",
    "platform",
    "protocol",
    "regulation",
    "service",
    "system",
    "technology",
    "公司",
    "平台",
    "数据库",
    "框架",
    "协议",
    "法规",
    "法律",
    "系统",
    "技术",
    "模型",
)


@dataclass(frozen=True)
class ExtractionChunk:
    start: int
    end: int
    text: str
    section: str
    score: float = 0.0
    hard_included: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "section": self.section,
            "score": round(float(self.score), 6),
            "hard_included": self.hard_included,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExtractionChunk":
        return cls(
            start=int(value.get("start", 0)),
            end=int(value.get("end", 0)),
            text=str(value.get("text") or ""),
            section=str(value.get("section") or "Document"),
            score=float(value.get("score", 0.0)),
            hard_included=bool(value.get("hard_included", False)),
        )


@dataclass(frozen=True)
class ExtractionSelection:
    text: str
    chunks: list[ExtractionChunk]
    original_character_count: int
    selected_character_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "chunks": [chunk.to_dict() for chunk in self.chunks],
            "original_character_count": self.original_character_count,
            "selected_character_count": self.selected_character_count,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExtractionSelection":
        chunks = value.get("chunks")
        return cls(
            text=str(value.get("text") or ""),
            chunks=[
                ExtractionChunk.from_dict(item)
                for item in chunks or []
                if isinstance(item, dict)
            ],
            original_character_count=int(value.get("original_character_count", 0)),
            selected_character_count=int(value.get("selected_character_count", 0)),
        )


class TemporaryExtractionStore:
    """Job-scoped extraction input that is never part of canonical property data."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _safe_id(value: str, label: str) -> str:
        if not value or Path(value).name != value:
            raise ValueError(f"Invalid {label}")
        return value

    def path(self, project_id: str, job_id: str, property_id: str) -> Path:
        project_id = self._safe_id(project_id, "project id")
        job_id = self._safe_id(job_id, "job id")
        property_id = self._safe_id(property_id, "property id")
        return (
            self.settings.projects_dir
            / project_id
            / "jobs"
            / "extraction-text"
            / job_id
            / f"{property_id}.json"
        )

    def _validated_path(self, path: Path | str) -> Path:
        target = Path(path).resolve()
        root = self.settings.projects_dir.resolve()
        if not target.is_relative_to(root) or "extraction-text" not in target.parts:
            raise ValueError("Invalid temporary extraction path")
        return target

    def save(
        self,
        project_id: str,
        job_id: str,
        property_id: str,
        selection: ExtractionSelection | dict[str, Any],
    ) -> Path:
        target = self.path(project_id, job_id, property_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = selection.to_dict() if hasattr(selection, "to_dict") else selection
        if not isinstance(payload, dict):
            raise TypeError("Temporary extraction selection must be an object")
        fd, temporary = tempfile.mkstemp(
            prefix="extraction-text-", suffix=".json", dir=target.parent
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

    def load(self, path: Path | str) -> ExtractionSelection:
        source = self._validated_path(path)
        parsed = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("Invalid temporary extraction selection")
        return ExtractionSelection.from_dict(parsed)

    def delete(self, path: Path | str | None) -> None:
        if not path:
            return
        target = self._validated_path(path)
        target.unlink(missing_ok=True)
        parent = target.parent
        extraction_root = parent.parent
        if parent.is_dir() and extraction_root.name == "extraction-text":
            try:
                parent.rmdir()
            except OSError:
                pass
            if extraction_root.is_dir() and not any(extraction_root.iterdir()):
                shutil.rmtree(extraction_root)


def _trimmed_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _heading_text(value: str) -> str | None:
    stripped = value.strip()
    markdown = re.match(r"^#{1,6}\s+(.+?)\s*#*$", stripped)
    if markdown:
        return markdown.group(1).strip()
    if (
        2 <= len(stripped) <= 100
        and not stripped.endswith((".", "?", "!", "。", "？", "！"))
        and (
            stripped.endswith(":")
            or stripped.isupper()
            or bool(re.match(r"^\d+(?:\.\d+)*[.)]?\s+\S+", stripped))
        )
    ):
        return stripped.rstrip(":").strip()
    return None


def _is_table_row(value: str) -> bool:
    stripped = value.strip()
    return stripped.count("|") >= 2 or bool(re.match(r"^\s*[^\t]+\t[^\t]+", value))


def _split_long_span(
    source: str, start: int, end: int, section: str
) -> list[ExtractionChunk]:
    start, end = _trimmed_span(source, start, end)
    if start >= end:
        return []
    if end - start <= _CHUNK_TARGET_CHARS:
        return [ExtractionChunk(start, end, source[start:end], section)]
    chunks: list[ExtractionChunk] = []
    cursor = start
    sentence_ends = [
        start + match.end()
        for match in re.finditer(r".*?(?:[.!?。！？](?:\s+|$)|\n+)", source[start:end], re.S)
        if match.group(0).strip()
    ]
    sentence_ends.append(end)
    while cursor < end:
        target = min(end, cursor + _CHUNK_TARGET_CHARS)
        candidates = [position for position in sentence_ends if cursor < position <= target]
        cut = max(candidates) if candidates else target
        if cut <= cursor:
            cut = min(end, cursor + _CHUNK_TARGET_CHARS)
        chunk_start, chunk_end = _trimmed_span(source, cursor, cut)
        if chunk_start < chunk_end:
            chunks.append(
                ExtractionChunk(
                    chunk_start,
                    chunk_end,
                    source[chunk_start:chunk_end],
                    section,
                )
            )
        cursor = cut
    return chunks


def split_extraction_chunks(source: str) -> list[ExtractionChunk]:
    if not source or not source.strip():
        return []
    chunks: list[ExtractionChunk] = []
    paragraph_start: int | None = None
    paragraph_end: int | None = None
    section = "Document"

    def flush_paragraph() -> None:
        nonlocal paragraph_start, paragraph_end
        if paragraph_start is not None and paragraph_end is not None:
            chunks.extend(
                _split_long_span(source, paragraph_start, paragraph_end, section)
            )
        paragraph_start = None
        paragraph_end = None

    for match in re.finditer(r"[^\n]*(?:\n|$)", source):
        raw = match.group(0)
        line_start = match.start()
        line_end = match.end()
        stripped = raw.strip()
        if not stripped:
            flush_paragraph()
            continue
        heading = _heading_text(raw)
        if heading is not None:
            flush_paragraph()
            trimmed_start, trimmed_end = _trimmed_span(source, line_start, line_end)
            chunks.append(
                ExtractionChunk(
                    trimmed_start,
                    trimmed_end,
                    source[trimmed_start:trimmed_end],
                    heading,
                )
            )
            section = heading
            continue
        if _is_table_row(raw):
            flush_paragraph()
            trimmed_start, trimmed_end = _trimmed_span(source, line_start, line_end)
            if trimmed_start < trimmed_end:
                chunks.append(
                    ExtractionChunk(
                        trimmed_start,
                        trimmed_end,
                        source[trimmed_start:trimmed_end],
                        section,
                    )
                )
            continue
        paragraph_start = line_start if paragraph_start is None else paragraph_start
        paragraph_end = line_end
    flush_paragraph()
    return chunks


def _normalized_tokens(value: str) -> list[str]:
    normalized = re.sub(r"[_/\\.-]+", " ", str(value or "").casefold())
    return [
        token
        for token in jieba.cut(normalized, cut_all=False)
        if token.strip()
        and token not in _STOPWORDS
        and re.search(r"[a-z0-9\u3400-\u9fff]", token)
    ]


def _token_set(value: str) -> set[str]:
    return set(_normalized_tokens(value))


def _character_ngrams(value: str, size: int = 3) -> set[str]:
    compact = re.sub(r"\s+", "", value.casefold())
    if len(compact) <= size:
        return {compact} if compact else set()
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def _similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = _token_set(left), _token_set(right)
    token_score = (
        len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
        if left_tokens and right_tokens
        else 0.0
    )
    left_grams, right_grams = _character_ngrams(left), _character_ngrams(right)
    gram_score = (
        len(left_grams & right_grams) / len(left_grams | right_grams)
        if left_grams and right_grams
        else 0.0
    )
    return 0.7 * token_score + 0.3 * gram_score


def _entity_aliases(existing_entities: Iterable[dict[str, Any]]) -> list[str]:
    aliases: list[str] = []
    for entity in existing_entities:
        values: list[Any] = [entity.get("id"), entity.get("name")]
        raw_aliases = entity.get("aliases") or entity.get("alias") or []
        values.extend(raw_aliases if isinstance(raw_aliases, list) else [raw_aliases])
        for value in values:
            alias = str(value or "").strip()
            if len(alias) >= 2 and alias.casefold() not in {
                current.casefold() for current in aliases
            }:
                aliases.append(alias)
    return aliases


def _mentions(text: str, phrase: str) -> bool:
    escaped = re.escape(phrase)
    pattern = escaped if re.search(r"[\u3400-\u9fff]", phrase) else rf"\b{escaped}\b"
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def _noise_penalty(
    chunk: ExtractionChunk, repeated_lines: Counter[str]
) -> float:
    stripped = chunk.text.strip()
    normalized = re.sub(r"\s+", " ", stripped.casefold())
    if not normalized:
        return 1.0
    penalty = 0.0
    if re.fullmatch(
        r"(?:page\s*)?\d+\s*(?:of|/|共)\s*\d+(?:\s*页)?|第?\s*\d+\s*页",
        normalized,
    ):
        return 1.0
    if normalized in _NAVIGATION_WORDS:
        return 1.0
    if repeated_lines[normalized] >= 2 and len(normalized) <= 120:
        penalty += 0.75
    urls = len(re.findall(r"https?://\S+|www\.\S+", stripped, flags=re.I))
    if urls and len(re.sub(r"https?://\S+|www\.\S+", "", stripped).strip()) < 30:
        penalty += 0.8
    if re.fullmatch(r"[a-f0-9]{24,}", normalized):
        penalty += 1.0
    visible = [character for character in stripped if not character.isspace()]
    if visible:
        symbols = sum(
            not character.isalnum()
            and not re.match(r"[\u3400-\u9fff]", character)
            for character in visible
        )
        if symbols / len(visible) > 0.48:
            penalty += 0.7
    code_markers = len(re.findall(r"[{}();=<>]|\b(?:const|def|class|return|import)\b", stripped))
    if code_markers >= 6 and not any(cue in normalized for cue in _RELATION_CUES):
        penalty += 0.35
    return min(1.0, penalty)


def _specific_noun_density(
    chunk: ExtractionChunk,
    token_frequencies: Counter[str],
    chunk_count: int,
) -> float:
    tokens = _normalized_tokens(chunk.text)
    if not tokens:
        return 0.0
    specific = 0.0
    for token in tokens:
        rarity = math.log((chunk_count + 1) / (token_frequencies[token] + 1)) + 1.0
        original_named = bool(
            re.search(
                rf"\b{re.escape(token)}\b",
                chunk.text,
                flags=0 if any(char.isupper() for char in token) else re.IGNORECASE,
            )
        ) and (
            any(char.isupper() for char in chunk.text if char.isalpha())
            or token.isupper()
        )
        professional = any(
            token.casefold().endswith(suffix) or suffix in token.casefold()
            for suffix in _PROFESSIONAL_SUFFIXES
        )
        long_specific = len(token) >= 6 and token not in _STOPWORDS
        if original_named or professional or long_specific or rarity > 1.6:
            specific += min(2.2, rarity)
    return min(1.0, specific / max(2.0, len(tokens) * 0.75))


def _section_importance(section: str, position: int, total: int) -> float:
    tokens = _token_set(section)
    if tokens & _IMPORTANT_SECTION_WORDS:
        return 1.0
    if tokens & _LOW_VALUE_SECTION_WORDS:
        return 0.05
    if position == 0:
        return 0.8
    if total > 1 and position >= int(total * 0.9):
        return 0.25
    return 0.5


def _relation_density(text: str) -> float:
    normalized = text.casefold()
    matches = sum(normalized.count(cue) for cue in _RELATION_CUES)
    sentence_count = max(1, len(re.findall(r"[.!?。！？]", text)))
    return min(1.0, matches / sentence_count)


def _selection_length(chunks: list[ExtractionChunk]) -> int:
    return sum(len(chunk.text) for chunk in chunks) + max(0, len(chunks) - 1) * 2


def _fits(chunks: list[ExtractionChunk], candidate: ExtractionChunk, budget: int) -> bool:
    return _selection_length([*chunks, candidate]) <= budget


def select_extraction_text(
    content: str,
    *,
    filename: str = "",
    definition: str = "",
    import_context: str = "",
    existing_entities: list[dict[str, Any]] | None = None,
    max_chars: int = DEFAULT_EXTRACTION_TEXT_MAX_CHARS,
) -> ExtractionSelection:
    source = str(content or "")
    budget = max(1, int(max_chars))
    chunks = split_extraction_chunks(source)
    if not chunks:
        return ExtractionSelection("", [], len(source), 0)

    line_counts: Counter[str] = Counter(
        re.sub(r"\s+", " ", line.strip().casefold())
        for line in source.splitlines()
        if line.strip()
    )
    token_frequencies: Counter[str] = Counter()
    for chunk in chunks:
        tokens = _token_set(chunk.text)
        token_frequencies.update(tokens)

    metadata = " ".join(
        part
        for part in (
            Path(filename).stem,
            definition,
            import_context,
        )
        if part
    )
    aliases = _entity_aliases(existing_entities or [])
    scored: list[ExtractionChunk] = []
    for index, chunk in enumerate(chunks):
        mentioned_aliases = [alias for alias in aliases if _mentions(chunk.text, alias)]
        existing_score = min(1.0, len(mentioned_aliases) / max(1, min(3, len(aliases))))
        noun_score = _specific_noun_density(chunk, token_frequencies, len(chunks))
        relation_score = _relation_density(chunk.text)
        metadata_score = _similarity(chunk.text, metadata) if metadata else 0.0
        section_score = _section_importance(chunk.section, index, len(chunks))
        noise_penalty = _noise_penalty(chunk, line_counts)
        score = max(
            0.0,
            0.30 * existing_score
            + 0.25 * noun_score
            + 0.20 * relation_score
            + 0.15 * metadata_score
            + 0.10 * section_score
            - noise_penalty,
        )
        scored.append(
            replace(
                chunk,
                score=score,
                hard_included=bool(mentioned_aliases),
            )
        )

    useful = [
        chunk
        for chunk in scored
        if chunk.hard_included or chunk.score > 0.035
    ]
    if not useful:
        useful = [max(scored, key=lambda chunk: chunk.score)]

    selected: list[ExtractionChunk] = []
    for alias in aliases:
        candidates = [
            chunk
            for chunk in useful
            if chunk.hard_included
            and _mentions(chunk.text, alias)
            and chunk not in selected
        ]
        if not candidates:
            continue
        candidate = max(candidates, key=lambda chunk: chunk.score)
        if _fits(selected, candidate, budget):
            selected.append(candidate)

    for candidate in sorted(
        (chunk for chunk in useful if chunk.hard_included and chunk not in selected),
        key=lambda chunk: (-chunk.score, chunk.start),
    ):
        if _fits(selected, candidate, budget):
            selected.append(candidate)

    remaining = [chunk for chunk in useful if chunk not in selected]
    section_characters: Counter[str] = Counter(
        {chunk.section: len(chunk.text) for chunk in selected}
    )
    while remaining:
        ranked: list[tuple[float, ExtractionChunk]] = []
        for candidate in remaining:
            redundancy = max(
                (_similarity(candidate.text, chosen.text) for chosen in selected),
                default=0.0,
            )
            section_share = (
                section_characters[candidate.section] / budget if budget else 0.0
            )
            coverage_penalty = (
                0.18
                if section_share >= _SECTION_BUDGET_SHARE
                and any(
                    other.section != candidate.section
                    for other in remaining
                    if _fits(selected, other, budget)
                )
                else 0.0
            )
            ranked.append(
                (
                    candidate.score
                    - _MMR_REDUNDANCY_WEIGHT * redundancy
                    - coverage_penalty,
                    candidate,
                )
            )
        _, best = max(ranked, key=lambda item: (item[0], item[1].score, -item[1].start))
        remaining.remove(best)
        if _fits(selected, best, budget):
            selected.append(best)
            section_characters[best.section] += len(best.text)

    if not selected:
        shortest = min(useful, key=lambda chunk: len(chunk.text))
        if len(shortest.text) > budget:
            clipped_text = shortest.text[:budget].rstrip()
            selected = [
                replace(shortest, end=shortest.start + len(clipped_text), text=clipped_text)
            ]
        else:
            selected = [shortest]

    selected.sort(key=lambda chunk: chunk.start)
    selected_text = "\n\n".join(chunk.text for chunk in selected)
    return ExtractionSelection(
        selected_text,
        selected,
        len(source),
        len(selected_text),
    )


def dump_extraction_selection(selection: ExtractionSelection) -> str:
    return json.dumps(selection.to_dict(), ensure_ascii=False, indent=2) + "\n"
