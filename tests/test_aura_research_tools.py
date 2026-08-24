from types import SimpleNamespace

from aura_music_studio import aura_research_tools as research


def test_select_diverse_prefers_distinct_domains():
    rows = [
        {"url": "https://one.example/a", "title": "1a"},
        {"url": "https://one.example/b", "title": "1b"},
        {"url": "https://two.example/a", "title": "2a"},
        {"url": "https://three.example/a", "title": "3a"},
    ]
    selected = research._select_diverse(rows, 3)
    assert [row["title"] for row in selected] == ["1a", "2a", "3a"]


def test_research_fetches_each_selected_url_once(monkeypatch):
    rows = [
        {"url": "https://one.example/a", "title": "One", "content": "one snippet"},
        {"url": "https://two.example/a", "title": "Two", "content": "two snippet"},
        {"url": "https://three.example/a", "title": "Three", "content": "three snippet"},
    ]

    class FakeGateway:
        def search(self, query, limit=10):
            return rows

    calls = []

    def fake_fetch(row):
        calls.append(row["url"])
        return {
            "title": row["title"],
            "url": row["url"],
            "content": "fetched " + row["title"],
            "fetched": True,
        }

    monkeypatch.setattr(research.tools, "AuraWebGateway", FakeGateway)
    monkeypatch.setattr(research, "_fetch_one", fake_fetch)
    registry = SimpleNamespace(_aura_source_counter=0)

    result = research._research(registry, "test research", 3)
    assert sorted(calls) == sorted(row["url"] for row in rows)
    assert len(calls) == 3
    assert [item["source_id"] for item in result] == ["S1", "S2", "S3"]


def test_research_source_counter_continues_existing_ids(monkeypatch):
    rows = [{"url": "https://one.example/a", "title": "One", "content": "snippet"}]

    class FakeGateway:
        def search(self, query, limit=10):
            return rows

    monkeypatch.setattr(research.tools, "AuraWebGateway", FakeGateway)
    monkeypatch.setattr(research, "_fetch_one", lambda row: {**row, "fetched": True})
    registry = SimpleNamespace(_aura_source_counter=4)
    result = research._research(registry, "test", 2)
    assert result[0]["source_id"] == "S5"
