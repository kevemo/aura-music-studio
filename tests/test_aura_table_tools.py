from pathlib import Path

import pytest

from aura_music_studio.aura_table_tools import _profile, _select_table, _svg_chart
from aura_music_studio.creative_project import CreativeProjectStore, CreativeReference


def test_profile_detects_numeric_and_categorical_columns():
    headers = ["name", "revenue", "cost"]
    rows = [["A", "10", "4"], ["B", "20", "5"], ["C", "30", "7"]]
    result = _profile(headers, rows)
    assert result["columns"]["name"]["type"] == "categorical_or_text"
    assert result["columns"]["revenue"]["type"] == "numeric"
    assert result["columns"]["revenue"]["mean"] == pytest.approx(20.0)
    assert result["correlations"][0]["left"] in {"revenue", "cost"}


def test_select_table_resolves_single_creative_reference(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    source = project / "input" / "references" / "metrics.csv"
    source.parent.mkdir(parents=True)
    source.write_text("name,value\nA,1\n", encoding="utf-8")
    store = CreativeProjectStore(project)
    store.initialize(project_name="demo", title="Demo")
    store.add_reference(CreativeReference(
        kind="reference",
        label="Metrics",
        source_ref="input/references/metrics.csv",
        rights_confirmed=True,
    ))
    selected = _select_table(project, None)
    assert selected["path"] == source.resolve()


def test_select_table_refuses_ambiguous_project_tables(tmp_path):
    project = tmp_path / "demo"
    refs = project / "input" / "references"
    refs.mkdir(parents=True)
    (refs / "one.csv").write_text("a\n1\n", encoding="utf-8")
    (refs / "two.csv").write_text("a\n2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        _select_table(project, None)


def test_svg_chart_escapes_labels_and_contains_no_script():
    svg = _svg_chart(["label", "value"], [["<script>alert(1)</script>", 4], ["B", 8]], "label", "value", "bar", 20)
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    assert svg.startswith("<svg")
