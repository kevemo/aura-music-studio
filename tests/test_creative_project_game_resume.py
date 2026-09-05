from aura_music_studio.creative_project_continuity import PROJECT_CONTINUITY_SCRIPT


def test_one_project_workspace_surfaces_project_bound_game_status_and_resume_link():
    script = PROJECT_CONTINUITY_SCRIPT

    assert "creativeProjectGameContext" in script
    assert "creativeProjectResumeGame" in script
    assert "refreshGameProjectSummary" in script
    assert "/api/game-forge/projects/${encodeURIComponent(clean)}/games" in script
    assert "Game Forge: no Game DNA yet" in script
    assert "Game Forge status unavailable" in script
    assert "Resume latest game" in script
    assert "gameSummaryToken" in script
    assert "token!==gameSummaryToken||currentProject()!==clean" in script


def test_game_resume_href_carries_both_creative_project_and_game_identity():
    script = PROJECT_CONTINUITY_SCRIPT

    assert "function projectGameHref(gameId,projectName=currentProject())" in script
    assert "query.set('project',cleanProject)" in script
    assert "query.set('game',cleanGame)" in script
    assert "return '/game-creation'+(encoded?`?${encoded}`:'')" in script
    assert "resume.href=projectGameHref(latest.id,clean)" in script


def test_requested_game_reopens_through_existing_safe_workspace_resolution():
    script = PROJECT_CONTINUITY_SCRIPT

    assert "const requestedGame=(params.get('game')||'').trim();" in script
    assert "if(requestedGame&&typeof openWorkspace==='function')" in script
    assert "await openWorkspace(requestedGame)" in script
    # openWorkspace is wrapped before bootRequestedProject runs, so the deep link still passes
    # through resolveGameProject and its server-side binding/rebinding protections.
    assert script.index("wrapGameWorkspace();") < script.index("void bootRequestedProject();")
    assert "try{await resolveGameProject(gameId)}" in script


def test_workspace_refreshes_game_summary_when_project_context_changes():
    script = PROJECT_CONTINUITY_SCRIPT

    assert "void refreshGameProjectSummary(projectName);" in script
    assert "function commitProject(projectName)" in script
    assert "updateLocation(clean);" in script
    assert "drawWorkspace();" in script
