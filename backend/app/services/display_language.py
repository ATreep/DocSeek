from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterable, Iterator, TypeVar


DEFAULT_DISPLAY_LANGUAGE = "English"
DISPLAY_LANGUAGE_HEADER = "X-DocSeek-Language"
_display_language: ContextVar[str] = ContextVar(
    "docseek_display_language",
    default=DEFAULT_DISPLAY_LANGUAGE,
)
T = TypeVar("T")


def normalize_display_language(value: object) -> str:
    normalized = str(value or "").strip().casefold().replace("_", "-")
    if normalized == "chinese" or normalized.startswith("zh"):
        return "Chinese"
    return DEFAULT_DISPLAY_LANGUAGE


def current_display_language() -> str:
    return _display_language.get()


@contextmanager
def display_language_scope(language: object):
    token = _display_language.set(normalize_display_language(language))
    try:
        yield
    finally:
        _display_language.reset(token)


def language_instruction(language: object | None = None) -> str:
    selected = (
        current_display_language()
        if language is None
        else normalize_display_language(language)
    )
    return f"Output your results in language {selected}."


def localized_messages(
    messages: list[dict[str, Any]],
    *,
    include: bool = True,
    language: object | None = None,
) -> list[dict[str, Any]]:
    localized = [dict(message) for message in messages]
    if not include or not localized:
        return localized
    instruction = language_instruction(language)
    for index in range(len(localized) - 1, -1, -1):
        message = localized[index]
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            if instruction not in content:
                message["content"] = f"{content.rstrip()}\n\n{instruction}"
            return localized
        if isinstance(content, list):
            blocks = [dict(block) if isinstance(block, dict) else block for block in content]
            if not any(
                isinstance(block, dict)
                and block.get("type") == "text"
                and instruction in str(block.get("text") or "")
                for block in blocks
            ):
                blocks.append({"type": "text", "text": instruction})
            message["content"] = blocks
            return localized
    localized.append({"role": "user", "content": instruction})
    return localized


def run_in_display_language(
    language: object,
    function: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    with display_language_scope(language):
        return function(*args, **kwargs)


def iterate_in_display_language(
    language: object,
    iterable: Iterable[T],
) -> Iterator[T]:
    iterator = iter(iterable)
    while True:
        with display_language_scope(language):
            try:
                item = next(iterator)
            except StopIteration:
                return
        yield item
