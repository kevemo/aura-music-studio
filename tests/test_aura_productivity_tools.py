import pytest

from aura_music_studio.aura_productivity_tools import safe_calculate, source_markdown, source_records, statistics_summary


def test_safe_calculate_supports_arithmetic_without_code_execution():
    result = safe_calculate("(1250 * 0.2) + 47.5")
    assert result["result"] == pytest.approx(297.5)
    assert result["code_execution"] is False


def test_safe_calculate_rejects_python_execution_syntax():
    with pytest.raises(ValueError):
        safe_calculate("__import__('os').system('echo nope')")


def test_safe_calculate_limits_exponents():
    with pytest.raises(ValueError):
        safe_calculate("2 ** 1001")


def test_statistics_summary_is_descriptive_and_finite():
    result = statistics_summary([1, 2, 3, 4, 5])
    assert result["count"] == 5
    assert result["mean"] == pytest.approx(3.0)
    assert result["median"] == pytest.approx(3.0)
    assert result["minimum"] == 1
    assert result["maximum"] == 5


def test_source_records_deduplicate_urls_and_preserve_tool_ids():
    tool_results = [
        {
            "tool": "web_search",
            "ok": True,
            "result": [
                {"source_id": "S1", "title": "One", "url": "https://example.com/one", "content": "a"},
                {"source_id": "S2", "title": "Duplicate", "url": "https://example.com/one", "content": "b"},
                {"source_id": "S3", "title": "Two", "url": "https://example.com/two", "content": "c"},
            ],
        }
    ]
    records = source_records(tool_results)
    assert [item["source_id"] for item in records] == ["S1", "S3"]
    assert len(records) == 2
    markdown = source_markdown(tool_results)
    assert "https://example.com/one" in markdown
    assert "https://example.com/two" in markdown
