from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .game_forge_models import GameDNA
from .plans import GAME_CREATE, GAME_CREATE_UNLIMITED
from .tenant_storage import ROOT, projects_root, safe_project_name


PRIVATE_GAMES_DIR = "_games"
PUBLIC_GAMES_ROOT = (ROOT / "_public_games").resolve()
PUBLIC_GAMES_ROOT.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def games_root() -> Path:
    root = (projects_root() / PRIVATE_GAMES_DIR).resolve()
    parent = projects_root().resolve()
    if parent not in root.parents:
        raise ValueError("Game storage escaped the member tenant")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_game_id(game_id: str) -> str:
    value = safe_project_name(game_id)
    if not value.startswith("game_"):
        raise ValueError("Invalid game id")
    return value


def game_dir(game_id: str, *, must_exist: bool = True) -> Path:
    root = games_root().resolve()
    target = (root / _safe_game_id(game_id)).resolve()
    if root not in target.parents:
        raise ValueError("Game path escaped the member tenant")
    if must_exist and (not target.is_dir()):
        raise FileNotFoundError(game_id)
    return target


def _manifest_path(game_id: str) -> Path:
    return game_dir(game_id) / "game_dna.json"


def load_game(game_id: str) -> GameDNA:
    path = _manifest_path(game_id)
    return GameDNA.model_validate_json(path.read_text(encoding="utf-8"))


def save_game(game: GameDNA) -> GameDNA:
    folder = game_dir(game.id, must_exist=False)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "game_dna.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(game.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    return game


def list_games() -> list[GameDNA]:
    root = games_root()
    rows: list[GameDNA] = []
    for folder in root.iterdir():
        path = folder / "game_dna.json"
        if not folder.is_dir() or not path.is_file():
            continue
        try:
            rows.append(GameDNA.model_validate_json(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return sorted(rows, key=lambda row: row.updated_at, reverse=True)


def active_editable_games() -> list[GameDNA]:
    return [row for row in list_games() if row.actively_editable]


def enforce_creation_entitlement(member) -> None:
    if not member.plan.has(GAME_CREATE):
        raise PermissionError("Game creation unlocks on the Basic £4.99 tier")
    if member.plan.has(GAME_CREATE_UNLIMITED):
        return
    if active_editable_games():
        raise PermissionError(
            "Basic includes one active editable game at a time. Finish/archive the current game before starting another, or upgrade to Pro for unlimited active game projects."
        )


def create_game(member, game: GameDNA) -> GameDNA:
    enforce_creation_entitlement(member)
    folder = game_dir(game.id, must_exist=False)
    if folder.exists():
        raise FileExistsError(game.id)
    folder.mkdir(parents=True, exist_ok=False)
    save_game(game)
    return game


def public_dir(public_id: str, *, must_exist: bool = True) -> Path:
    value = safe_project_name(public_id)
    if not value.startswith("public_game_"):
        raise ValueError("Invalid public game id")
    target = (PUBLIC_GAMES_ROOT / value).resolve()
    if PUBLIC_GAMES_ROOT not in target.parents:
        raise ValueError("Public game path escaped storage")
    if must_exist and not target.is_dir():
        raise FileNotFoundError(public_id)
    return target


def remove_public_snapshot(game: GameDNA) -> None:
    if not game.public_id:
        return
    try:
        target = public_dir(game.public_id, must_exist=False)
    except ValueError:
        return
    if target.is_dir():
        shutil.rmtree(target)


def publish_snapshot(game: GameDNA, play_html: str) -> str:
    public_id = game.public_id or f"public_{game.id}"
    # Convert game_abc -> public_game_abc for the strict public-id namespace.
    if not public_id.startswith("public_game_"):
        public_id = f"public_{game.id}"
    target = public_dir(public_id, must_exist=False)
    target.mkdir(parents=True, exist_ok=True)
    public_manifest = {
        "public_id": public_id,
        "title": game.title,
        "genre": game.genre,
        "niches": game.niches,
        "dimension": game.dimension,
        "engine_target": game.engine_target,
        "synopsis": game.synopsis,
        "suggested_age_band": game.rating_assessment.suggested_age_band if game.rating_assessment else None,
        "rating_note": game.rating_assessment.note if game.rating_assessment else None,
        "content_descriptors": game.rating_assessment.content_descriptors if game.rating_assessment else [],
        "published_at": _now(),
        "build_id": game.latest_build.build_id if game.latest_build else None,
        "content_hash": game.latest_build.content_hash if game.latest_build else None,
        "creator_private_data_included": False,
    }
    (target / "manifest.json").write_text(json.dumps(public_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (target / "play.html").write_text(play_html, encoding="utf-8")
    return public_id


def public_manifest(public_id: str) -> dict:
    path = public_dir(public_id) / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def public_play_html(public_id: str) -> str:
    return (public_dir(public_id) / "play.html").read_text(encoding="utf-8")


def list_public_games() -> list[dict]:
    rows: list[dict] = []
    for folder in PUBLIC_GAMES_ROOT.iterdir():
        if not folder.is_dir():
            continue
        manifest = folder / "manifest.json"
        play = folder / "play.html"
        if not manifest.is_file() or not play.is_file():
            continue
        try:
            rows.append(json.loads(manifest.read_text(encoding="utf-8")))
        except Exception:
            continue
    return sorted(rows, key=lambda row: row.get("published_at", ""), reverse=True)
