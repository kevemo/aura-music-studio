import pytest

from aura_music_studio.game_forge_package_integrity import _canonical_package_path


@pytest.mark.parametrize(
    "value",
    (
        "media//asset.wav",
        "media/./asset.wav",
        "media/../asset.wav",
        "../asset.wav",
        "/media/asset.wav",
        "C:/media/asset.wav",
        "media\\asset.wav",
        "media/asset.wav/",
    ),
)
def test_game_export_package_paths_reject_noncanonical_or_unsafe_forms(value):
    with pytest.raises(ValueError, match="unsafe path|non-canonical"):
        _canonical_package_path(value, context="test package path")


def test_game_export_package_paths_accept_canonical_posix_members():
    assert _canonical_package_path("media/asset.wav", context="test package path") == "media/asset.wav"
    assert _canonical_package_path("index.html", context="test package path") == "index.html"
