from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")
DEFAULT_MODEL_ATTEMPTS = 3


def retry_model_call(
    operation: Callable[[], T], *, attempts: int = DEFAULT_MODEL_ATTEMPTS
) -> T:
    """Retry one complete model operation, including response validation."""
    if attempts < 1:
        raise ValueError("attempts must be at least one")
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error
