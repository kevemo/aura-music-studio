from __future__ import annotations

import pytest

from aura_music_studio.aura_effect_system_creator import (
    MAX_EFFECT_NODES,
    EffectNodeSpec,
    compile_effect_system,
    compile_effect_system_payload,
    make_effect_system,
)


def test_compiles_real_catalogue_nodes_into_ffmpeg_chain():
    spec = make_effect_system(
        "vocal.polish.v1",
        "Vocal Polish",
        (
            EffectNodeSpec("clean-low", "music.fx.highpass", {"hz": 85}),
            EffectNodeSpec("compress", "music.fx.compressor", {"threshold_db": -20, "ratio": 3}),
            EffectNodeSpec("space", "music.fx.reverb", {"predelay_ms": 22, "mix": 0.14}),
        ),
    )
    compiled = compile_effect_system(spec)
    assert len(compiled.effects) == 3
    assert "highpass=" in compiled.ffmpeg_filter_chain
    assert "acompressor=" in compiled.ffmpeg_filter_chain
    assert "aecho=" in compiled.ffmpeg_filter_chain
    assert len(compiled.fingerprint) == 64
    assert compiled.public()["backend_executable"] is True
    assert compiled.public()["source_media_mutated"] is False


def test_catalogue_bounds_are_applied_by_runtime_contract():
    payload = compile_effect_system_payload(
        {
            "id": "safe.bounds",
            "name": "Safe Bounds",
            "nodes": [{"id": "gain", "catalogue_item_id": "music.fx.gain", "parameters": {"db": 999}}],
        }
    )
    assert payload["effects"][0]["parameters"]["db"] == 18.0
    assert "volume=18.0dB" in payload["ffmpeg_filter_chain"]


def test_unknown_parameter_fails_closed():
    spec = make_effect_system(
        "reject.unknown.parameter",
        "Reject Unknown Parameter",
        (EffectNodeSpec("gain", "music.fx.gain", {"shell": "nope"}),),
    )
    with pytest.raises(ValueError, match="Unsupported parameters"):
        compile_effect_system(spec)


def test_unknown_catalogue_item_fails_closed():
    spec = make_effect_system(
        "reject.unknown.item",
        "Reject Unknown Item",
        (EffectNodeSpec("mystery", "music.fx.not-real"),),
    )
    with pytest.raises(ValueError, match="Unknown executable catalogue item"):
        compile_effect_system(spec)


def test_duplicate_node_ids_fail_closed():
    with pytest.raises(ValueError, match="Duplicate effect node id"):
        make_effect_system(
            "duplicate.nodes",
            "Duplicate Nodes",
            (EffectNodeSpec("same", "music.fx.gain"), EffectNodeSpec("same", "music.fx.reverb")),
        )


def test_mix_must_be_bounded():
    with pytest.raises(ValueError, match="mix must be between 0 and 1"):
        make_effect_system("bad.mix", "Bad Mix", (EffectNodeSpec("gain", "music.fx.gain", mix=1.5),))


def test_disabled_nodes_remain_in_graph_but_compile_no_filter():
    spec = make_effect_system(
        "disabled.node",
        "Disabled Node",
        (EffectNodeSpec("gain", "music.fx.gain", {"db": 3}, enabled=False),),
    )
    compiled = compile_effect_system(spec)
    assert len(compiled.effects) == 1
    assert compiled.effects[0].enabled is False
    assert compiled.ffmpeg_filter_chain == ""


def test_fingerprint_is_deterministic_for_same_effective_graph():
    payload = {
        "id": "stable.graph",
        "name": "Stable Graph",
        "nodes": [
            {"id": "gain", "catalogue_item_id": "music.fx.gain", "parameters": {"db": 2}},
            {"id": "width", "catalogue_item_id": "music.fx.stereo_width", "parameters": {"width": 1.2}},
        ],
    }
    left = compile_effect_system_payload(payload)
    right = compile_effect_system_payload(payload)
    assert left["fingerprint"] == right["fingerprint"]
    assert left["ffmpeg_filter_chain"] == right["ffmpeg_filter_chain"]


def test_maximum_node_guard_is_enforced():
    nodes = tuple(EffectNodeSpec(f"gain-{index}", "music.fx.gain", {"db": 0}) for index in range(MAX_EFFECT_NODES + 1))
    with pytest.raises(ValueError, match="maximum node count"):
        make_effect_system("too.large", "Too Large", nodes)


def test_public_payload_contains_no_execution_command_surface():
    result = compile_effect_system_payload(
        {"id": "public.safe", "name": "Public Safe", "nodes": [{"id": "limiter", "catalogue_item_id": "music.fx.limiter"}]}
    )
    text = str(result).casefold()
    assert "subprocess" not in text
    assert "powershell" not in text
    assert "shell" not in text
    assert result["runtime"] == "ffmpeg_audio"
