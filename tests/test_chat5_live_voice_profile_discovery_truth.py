from aura_music_studio.shared_skies_live_voice import voice_runtime_truth


def test_live_voice_readiness_acknowledges_candidate_profile_discovery_without_overclaiming_runtime():
    truth = voice_runtime_truth({})
    selection = truth["authorised_voice_profile_selection"]

    assert selection["state"] == "integration_required"
    detail = selection["detail"].lower()
    assert "selection candidates" in detail
    assert "no server-authoritative processor binding" in detail
    assert "does not prove executable or real-time voice processing" in detail
    assert "no selectable profile list" not in detail
