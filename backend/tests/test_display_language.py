import importlib

import pytest


def display_language_module():
    try:
        return importlib.import_module("backend.app.services.display_language")
    except ModuleNotFoundError:
        pytest.fail("display-language support is not implemented")


def test_display_language_normalizes_supported_chinese_values():
    module = display_language_module()

    assert module.normalize_display_language("zh") == "Chinese"
    assert module.normalize_display_language("zh-CN") == "Chinese"
    assert module.normalize_display_language("Chinese") == "Chinese"
    assert module.normalize_display_language("en-US") == "English"
    assert module.normalize_display_language("unexpected") == "English"


def test_localized_messages_append_instruction_without_mutating_input():
    module = display_language_module()
    messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Return JSON."},
    ]

    with module.display_language_scope("Chinese"):
        localized = module.localized_messages(messages)

    assert localized[-1]["content"].endswith(
        "Output your results in language Chinese."
    )
    assert messages[-1]["content"] == "Return JSON."


def test_localized_messages_can_skip_identifier_generation():
    module = display_language_module()
    messages = [{"role": "user", "content": "Generate an ASCII identifier."}]

    with module.display_language_scope("Chinese"):
        localized = module.localized_messages(messages, include=False)

    assert localized == messages


def test_language_scoped_iterator_keeps_language_during_deferred_work():
    module = display_language_module()

    def values():
        yield module.current_display_language()

    scoped = module.iterate_in_display_language("Chinese", values())

    assert list(scoped) == ["Chinese"]
