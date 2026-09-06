from __future__ import annotations

import pytest

import aura_music_studio.aura_effect_system_creator as creator
from aura_music_studio.aura_effect_system_creator import EffectNodeSpec, compile_effect_system, make_effect_system


def _gain_system():
    return make_effect_system(
        "resource.safe",
        "Resource Safe",
        (EffectNodeSpec("gain", "music.fx.gain", {"db": 2}),),
    )


def test_compiled_effect_system_reports_server_resource_budget():
    compiled = compile_effect_system(_gain_system())
    budget = compiled.public()["resource_budget"]

    assert budget["node_count"] == 1
    assert budget["max_node_count"] == creator.MAX_EFFECT_NODES
    assert 0 < budget["canonical_graph_bytes"] <= budget["max_canonical_graph_bytes"]
    assert 0 < budget["filter_chain_chars"] <= budget["max_filter_chain_chars"]
    assert budget["max_canonical_graph_bytes"] == creator.MAX_CANONICAL_GRAPH_BYTES
    assert budget["max_filter_chain_chars"] == creator.MAX_FFMPEG_FILTER_CHAIN_CHARS


def test_canonical_graph_size_fails_closed_before_fingerprint(monkeypatch):
    monkeypatch.setattr(creator, "MAX_CANONICAL_GRAPH_BYTES", 1)

    with pytest.raises(ValueError, match="maximum canonical graph size"):
        compile_effect_system(_gain_system())


def test_compiled_filter_chain_size_fails_closed(monkeypatch):
    monkeypatch.setattr(creator, "MAX_FFMPEG_FILTER_CHAIN_CHARS", 8)

    with pytest.raises(ValueError, match="maximum compiled filter-chain size"):
        compile_effect_system(_gain_system())


def test_resource_limits_do_not_add_execution_authority():
    public = compile_effect_system(_gain_system()).public()
    budget = public["resource_budget"]

    assert set(budget) == {
        "node_count",
        "max_node_count",
        "canonical_graph_bytes",
        "max_canonical_graph_bytes",
        "filter_chain_chars",
        "max_filter_chain_chars",
    }
    assert "command" not in budget
    assert "shell" not in budget
    assert "provider_secret" not in budget
