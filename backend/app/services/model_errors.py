from __future__ import annotations

from .llm_invocation_logs import mark_llm_invocation_validation_failed


MAX_STORED_MODEL_RESPONSE_CHARS = 50_000


def bounded_model_response(value: object) -> str:
    response = str(value or "").strip()
    if len(response) <= MAX_STORED_MODEL_RESPONSE_CHARS:
        return response
    return (
        response[:MAX_STORED_MODEL_RESPONSE_CHARS]
        + "\n\n[Original LLM response truncated]"
    )


def attach_model_response(error: Exception, response: object) -> Exception:
    mark_llm_invocation_validation_failed(response)
    raw_response = bounded_model_response(response)
    if raw_response and not getattr(error, "llm_response", None):
        error.llm_response = raw_response
    return error


def extract_model_response(error: BaseException | None) -> str | None:
    current = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = bounded_model_response(getattr(current, "llm_response", ""))
        if response:
            return response
        current = current.__cause__ or current.__context__
    return None
