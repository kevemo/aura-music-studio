from aura_music_studio.creative_project_continuity import PROJECT_CONTINUITY_SCRIPT


def test_shared_project_bar_reports_truthful_game_build_and_public_test_state():
    script = PROJECT_CONTINUITY_SCRIPT

    assert "creativeProjectPlayGame" in script
    assert "creativeProjectPublicGame" in script
    assert "latest.public_id?'public test':(latest.latest_build?'build ready':'not built')" in script
    assert "Game Forge: ${games.length} game" in script


def test_private_play_link_is_only_exposed_when_current_plan_can_open_creator_playtest():
    script = PROJECT_CONTINUITY_SCRIPT

    assert "if(play&&payload.can_create&&latest.id&&latest.latest_build)" in script
    assert "play.href=`/game-creation/play/${encodeURIComponent(String(latest.id))}`" in script
    assert "play.hidden=false" in script


def test_public_test_link_uses_only_provider_persisted_public_game_identity():
    script = PROJECT_CONTINUITY_SCRIPT

    assert "if(publicPlay&&latest.public_id)" in script
    assert "publicPlay.href=`/game-gallery/${encodeURIComponent(String(latest.public_id))}`" in script
    assert "publicPlay.hidden=false" in script


def test_game_action_links_fail_closed_during_project_switches_and_api_failures():
    script = PROJECT_CONTINUITY_SCRIPT

    assert "function hideGameProjectActions()" in script
    assert "if(resume)resume.hidden=true" in script
    assert "if(play)play.hidden=true" in script
    assert "if(publicPlay)publicPlay.hidden=true" in script
    assert "if(token!==gameSummaryToken||currentProject()!==clean)return" in script
    assert "Game Forge status unavailable" in script
