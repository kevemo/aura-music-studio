from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from aura_music_studio import separation
from aura_music_studio.separation import _safe_extract_stem_archive


def _archive(path: Path, members: list[tuple[str, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, payload in members:
            z.writestr(name, payload)
    return path


def test_safe_extract_accepts_nested_stem_files(tmp_path: Path) -> None:
    archive = _archive(
        tmp_path / "stems.zip",
        [("model/vocals.wav", b"vocals"), ("model/drums.wav", b"drums")],
    )
    destination = tmp_path / "out"

    _safe_extract_stem_archive(archive, destination)

    assert (destination / "model" / "vocals.wav").read_bytes() == b"vocals"
    assert (destination / "model" / "drums.wav").read_bytes() == b"drums"


@pytest.mark.parametrize(
    "member_name",
    ["../escape.wav", "nested/../../escape.wav", "..\\escape.wav", "/escape.wav", "C:/escape.wav"],
)
def test_safe_extract_rejects_paths_outside_destination(tmp_path: Path, member_name: str) -> None:
    archive = _archive(tmp_path / "stems.zip", [(member_name, b"escape")])
    destination = tmp_path / "out"

    with pytest.raises(ValueError, match="Unsafe path"):
        _safe_extract_stem_archive(archive, destination)

    assert not (tmp_path / "escape.wav").exists()


def test_safe_extract_rejects_symlink_entries(tmp_path: Path) -> None:
    archive = tmp_path / "stems.zip"
    info = zipfile.ZipInfo("vocals.wav")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(info, "../../escape.wav")

    with pytest.raises(ValueError, match="Links are not allowed"):
        _safe_extract_stem_archive(archive, tmp_path / "out")


def test_safe_extract_validates_all_members_before_writing(tmp_path: Path) -> None:
    archive = _archive(
        tmp_path / "stems.zip",
        [("vocals.wav", b"valid"), ("../escape.wav", b"escape")],
    )
    destination = tmp_path / "out"

    with pytest.raises(ValueError, match="Unsafe path"):
        _safe_extract_stem_archive(archive, destination)

    assert not (destination / "vocals.wav").exists()
    assert not (tmp_path / "escape.wav").exists()


def test_safe_extract_rejects_excessive_entry_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(separation, "_MAX_ARCHIVE_FILES", 1)
    archive = _archive(tmp_path / "stems.zip", [("vocals.wav", b"v"), ("drums.wav", b"d")])

    with pytest.raises(ValueError, match="too many entries"):
        _safe_extract_stem_archive(archive, tmp_path / "out")


def test_safe_extract_rejects_excessive_uncompressed_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(separation, "_MAX_ARCHIVE_UNCOMPRESSED_BYTES", 3)
    archive = _archive(tmp_path / "stems.zip", [("vocals.wav", b"four")])

    with pytest.raises(ValueError, match="too large"):
        _safe_extract_stem_archive(archive, tmp_path / "out")
