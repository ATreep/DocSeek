from __future__ import annotations

import jieba
import re


PROPERTY_WORD_WARNING_LIMIT = 15_000
PROPERTY_CHARACTER_WARNING_LIMIT = 60_000


def jieba_word_count(text: str) -> int:
    return sum(
        1
        for token in jieba.cut(str(text or ""), cut_all=False)
        if re.search(r"[A-Za-z0-9\u3400-\u9fff]", token)
    )


def property_content_metrics(text: str) -> dict:
    content = str(text or "")
    word_count = jieba_word_count(content)
    character_count = len(content)
    reasons: list[str] = []
    if word_count > PROPERTY_WORD_WARNING_LIMIT:
        reasons.append("word_count")
    if character_count > PROPERTY_CHARACTER_WARNING_LIMIT:
        reasons.append("character_count")
    return {
        "word_count": word_count,
        "character_count": character_count,
        "oversized": bool(reasons),
        "reasons": reasons,
    }
