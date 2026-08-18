import pytest

from backend.app.services.retry import retry_model_call


def test_model_call_retries_twice_before_returning_the_third_result():
    attempts = []

    def operation():
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise ValueError("temporary invalid response")
        return "ready"

    assert retry_model_call(operation) == "ready"
    assert attempts == [1, 2, 3]


def test_model_call_raises_only_after_three_failed_attempts():
    attempts = []

    def operation():
        attempts.append(len(attempts) + 1)
        raise ValueError("still invalid")

    with pytest.raises(ValueError, match="still invalid"):
        retry_model_call(operation)

    assert attempts == [1, 2, 3]
