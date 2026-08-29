from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from aura_music_studio.game_forge_godot_export import create_godot_source_export
from aura_music_studio.game_forge_godot_validation import (
    GODOT_HEADLESS_LINUX_X86_64_BINARY,
    GODOT_HEADLESS_LINUX_X86_64_SHA256,
    GODOT_HEADLESS_LINUX_X86_64_URL,
    GODOT_HEADLESS_VERSION,
    validate_godot_pin,
)
from aura_music_studio.game_forge_models import GameBuild, GameDNA
from aura_music_studio.game_forge_world import GameWorldDNA, WorldEntityDNA


def _game(dimension: str, content_hash: str) -> GameDNA:
    suffix = "3d" if dimension == "3d" else "2d"
    game = GameDNA(
        id=f"game_godot_ci_{suffix}",
        title=f"Aura Godot CI {suffix.upper()}",
        prompt="Deterministic pinned-engine CI fixture",
        genre="adventure",
        dimension=dimension,
        engine_target="aura3d" if dimension == "3d" else "aura2d",
        rights_confirmed=True,
        rights_attestation="Repository-owned deterministic CI fixture.",
    )
    game.latest_build = GameBuild(
        build_id=f"build_godot_ci_{suffix}",
        content_hash=content_hash,
        requested_engine=game.engine_target,
        runtime=f"aura_game_runtime_ci_{suffix}",
    )
    return game


def _world(game: GameDNA) -> GameWorldDNA:
    return GameWorldDNA(
        world_id=f"world_{game.id}",
        game_id=game.id,
        dimension=game.dimension,
        name="Pinned Godot CI World",
        entities=[
            WorldEntityDNA(id="player", name="Player", kind="player"),
            WorldEntityDNA(id="ci_marker", name="CI Marker", kind="mesh" if game.dimension == "3d" else "sprite"),
        ],
    )


def _safe_extract(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with ZipFile(zip_path) as archive:
        for info in archive.infolist():
            member = Path(info.filename)
            if member.is_absolute() or ".." in member.parts:
                raise ValueError(f"Unsafe fixture archive member: {info.filename}")
            target = (root / member).resolve()
            if root != target and root not in target.parents:
                raise ValueError(f"Fixture archive escaped extraction root: {info.filename}")
        archive.extractall(root)


def build_fixture(output_root: Path, dimension: str) -> dict:
    suffix = "3d" if dimension == "3d" else "2d"
    content_hash = ("3" if dimension == "3d" else "2") * 64
    game = _game(dimension, content_hash)
    world = _world(game)
    archive_path = output_root / f"aura-godot-{suffix}.zip"
    project_dir = output_root / suffix
    if project_dir.exists():
        shutil.rmtree(project_dir)

    with (
        patch("aura_music_studio.game_forge_godot_export.game_integrity_hash", return_value=content_hash),
        patch("aura_music_studio.game_forge_godot_export.asset_publication_blockers", return_value=[]),
        patch("aura_music_studio.game_forge_godot_export.ensure_world", return_value=world),
        patch("aura_music_studio.game_forge_godot_export.runtime_asset_manifest", return_value=[]),
        patch("aura_music_studio.game_forge_godot_export._export_path", return_value=archive_path),
    ):
        result = create_godot_source_export(game)

    _safe_extract(archive_path, project_dir)
    expected = {"project.godot", "main.tscn", "main.gd", "game_dna.json", "world_dna.json", "adapter_manifest.json"}
    missing = sorted(name for name in expected if not (project_dir / name).is_file())
    if missing:
        raise RuntimeError(f"Generated Godot {suffix} fixture is missing: {', '.join(missing)}")
    return {
        "dimension": dimension,
        "project_dir": str(project_dir),
        "archive": str(archive_path),
        "export_id": result["export_id"],
        "sha256": result["sha256"],
        "production_ready": result["production_ready"],
        "runtime_parity_claimed": result["runtime_parity_claimed"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build representative Aura Godot source projects for pinned headless CI.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--print-engine-env", action="store_true")
    args = parser.parse_args()

    validate_godot_pin()
    if args.print_engine_env:
        print(f"GODOT_VERSION={GODOT_HEADLESS_VERSION}")
        print(f"GODOT_URL={GODOT_HEADLESS_LINUX_X86_64_URL}")
        print(f"GODOT_SHA256={GODOT_HEADLESS_LINUX_X86_64_SHA256}")
        print(f"GODOT_BINARY={GODOT_HEADLESS_LINUX_X86_64_BINARY}")
        return 0

    root = Path(args.output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    results = [build_fixture(root, "2d"), build_fixture(root, "3d")]
    manifest = {
        "godot_version": GODOT_HEADLESS_VERSION,
        "fixtures": results,
        "runtime_parity_claimed": False,
        "production_ready": False,
    }
    (root / "fixture-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
