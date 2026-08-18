from backend.app.services.text_metrics import property_content_metrics


def test_property_content_metrics_warns_only_above_character_limit():
    at_limit = property_content_metrics("a" * 60_000)
    above_limit = property_content_metrics("a" * 60_001)

    assert at_limit["oversized"] is False
    assert above_limit["oversized"] is True
    assert above_limit["character_count"] == 60_001
    assert "character_count" in above_limit["reasons"]


def test_property_content_metrics_warns_only_above_jieba_word_limit():
    at_limit = property_content_metrics(" ".join(["a"] * 15_000))
    above_limit = property_content_metrics(" ".join(["a"] * 15_001))

    assert at_limit["word_count"] == 15_000
    assert at_limit["oversized"] is False
    assert above_limit["word_count"] == 15_001
    assert above_limit["oversized"] is True
    assert "word_count" in above_limit["reasons"]


def test_property_content_metrics_does_not_count_punctuation_as_words():
    metrics = property_content_metrics("Atlas，Neo4j。。。！！！")

    assert metrics["word_count"] == 2
