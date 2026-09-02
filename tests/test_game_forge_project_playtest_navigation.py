from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from aura_music_studio.game_forge_api import (
    _game_creation_url,
    _host_page,
    _private_playtest_url,
)


def _game(*, project: str | None = None, game_id: str = "game 01"):
    metadata = {}
    if project is not None:
        metadata["creative_project_name"] = project
    return SimpleNamespace(id=game_id, metadata=metadata)


def test_project_bound_game_urls_preserve_exact_project_and_game_context():
    game = _game(project="Neon City & Beyond")

    editor_url = _game_creation_url(game)
    editor = urlparse(editor_url)
    assert editor.path == "/game-creation"
    assert parse_qs(editor.query) == {
        "project": ["Neon City & Beyond"],
        "game": ["game 01"],
    }

    play_url = _private_playtest_url(game)
    play = urlparse(play_url)
    assert play.path == "/game-creation/play/game%2001"
    assert parse_qs(play.query) == {
        "project": ["Neon City & Beyond"],
        "game": ["game 01"],
    }


def test_popout_url_keeps_project_context_and_marks_host_as_popout():
    game = _game(project="Project Alpha", game_id="game-123")
    parsed = urlparse(_private_playtest_url(game, popout=True))

    assert parsed.path == "/game-creation/play/game-123"
    assert parse_qs(parsed.query) == {
        "project": ["Project Alpha"],
        "game": ["game-123"],
        "popout": ["1"],
    }


def test_legacy_unbound_game_keeps_game_resume_context_without_inventing_project():
    game = _game(game_id="legacy-game")

    assert parse_qs(urlparse(_game_creation_url(game)).query) == {"game": ["legacy-game"]}
    assert parse_qs(urlparse(_private_playtest_url(game)).query) == {"game": ["legacy-game"]}


def test_host_page_uses_explicit_return_and_popout_urls_instead_of_frame_derivation():
    response = _host_page(
        "Neon Runner",
        "/api/game-forge/games/game-123/playtest-frame",
        rating_line="Private draft build",
        popout=False,
        return_url="/game-creation?project=Project+Alpha&game=game-123",
        popout_url="/game-creation/play/game-123?project=Project+Alpha&game=game-123&popout=1",
    )
    html = response.body.decode("utf-8")

    assert "href='/game-creation?project=Project+Alpha&amp;game=game-123'" in html
    assert "href='/game-creation/play/game-123?project=Project+Alpha&amp;game=game-123&amp;popout=1'" in html
    assert "target='_blank'" in html
    assert "rel='noopener noreferrer'" in html
    assert "window.open" not in html
    assert "playtest-frame' target='_blank'" not in html


def test_popout_host_does_not_offer_recursive_popout_action():
    response = _host_page(
        "Neon Runner",
        "/api/game-forge/games/game-123/playtest-frame",
        rating_line="Private draft build",
        popout=True,
        return_url="/game-creation?project=Project+Alpha&game=game-123",
        popout_url="/game-creation/play/game-123?project=Project+Alpha&game=game-123&popout=1",
    )
    html = response.body.decode("utf-8")

    assert "Pop Out for TikTok LIVE Studio" not in html
    assert "Game Creation" in html
