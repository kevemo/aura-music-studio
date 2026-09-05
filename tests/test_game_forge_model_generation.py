from __future__ import annotations

import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import aura_music_studio.game_forge_model_generation as generation
from aura_music_studio.game_forge_models import GameDNA


def _game() -> GameDNA:
    return GameDNA(
        id="game_generation_test",
        title="3D Generation Truth",
        prompt="Build a small 3D test world.",
        dimension="3d",
        engine_target="aura3d",
        rights_confirmed=True,
        rights_attestation="Creator confirmed project rights.",
    )


def _request():
    return SimpleNamespace(
        state=SimpleNamespace(member=SimpleNamespace(user_id="creator_generation")),
        headers={},
    )


def _install(monkeypatch, tmp_path, game: GameDNA):
    member = SimpleNamespace(user_id="creator_generation")
    monkeypatch.setattr(generation, "_creator", lambda _request: member)
    monkeypatch.setattr(generation, "load_game", lambda game_id: game if game_id == game.id else (_ for _ in ()).throw(FileNotFoundError(game_id)))
    monkeypatch.setattr(generation, "game_dir", lambda game_id: tmp_path / game_id)
    (tmp_path / game.id).mkdir(parents=True, exist_ok=True)
    return member


def test_unconfigured_provider_fails_honestly_and_never_creates_fake_asset(monkeypatch, tmp_path):
    game = _game()
    _install(monkeypatch, tmp_path, game)
    monkeypatch.delenv("AURA_GAME_3D_PROVIDER", raising=False)

    body = generation.CreateModelGenerationRequest(
        capability="text_to_3d",
        prompt="A low-poly observatory with a circular platform.",
    )
    with pytest.raises(HTTPException) as exc:
        generation.request_model_generation(game.id, body, _request())

    assert exc.value.status_code == 503
    detail = exc.value.detail
    assert detail["code"] == "generation_provider_unavailable"
    assert detail["job"]["state"] == "failed"
    assert detail["job"]["provider_result_ref"] is None
    assert detail["job"]["final_asset_version_ref"] is None
    assert detail["job"]["progress_percent"] is None
    assert detail["job"]["provenance"]["commercial_use_clearance_asserted"] is False
    stored = generation.list_generation_jobs(game.id)
    assert len(stored) == 1
    assert stored[0].state == "failed"


def test_configured_provider_queues_then_worker_transitions_only_on_reported_truth(monkeypatch, tmp_path):
    game = _game()
    _install(monkeypatch, tmp_path, game)
    monkeypatch.setenv("AURA_GAME_3D_PROVIDER", "fixture3d")
    monkeypatch.setenv("AURA_GAME_3D_PROVIDER_FIXTURE3D_ENABLED", "1")

    body = generation.CreateModelGenerationRequest(
        capability="text_to_3d",
        prompt="A stylised stone archway.",
        quality_profile="draft",
        target_poly_budget=12000,
        idempotency_key="archway_request_1",
    )
    created = generation.request_model_generation(game.id, body, _request())
    job = created["job"]
    assert job["state"] == "queued"
    assert job["progress_percent"] is None
    assert job["provider_result_ref"] is None
    assert created["provider_submission_deferred_to_worker"] is True

    replay = generation.request_model_generation(game.id, body, _request())
    assert replay["idempotent_replay"] is True
    assert replay["job"]["generation_request_id"] == job["generation_request_id"]

    claimed = generation.claim_generation_job(
        game.id,
        job["generation_request_id"],
        provider="fixture3d",
        correlation_id="corr_claim",
    )
    assert claimed.state == "running"
    assert claimed.progress_percent is None

    progressed = generation.report_generation_progress(
        game.id,
        job["generation_request_id"],
        provider="fixture3d",
        progress_percent=42,
        correlation_id="corr_progress",
    )
    assert progressed.state == "running"
    assert progressed.progress_percent == 42

    completed = generation.complete_generation_job(
        game.id,
        job["generation_request_id"],
        provider="fixture3d",
        provider_result_ref="provider_result:fixture_1",
        final_asset_version_ref="model_asset:validated_v1",
        correlation_id="corr_complete",
    )
    assert completed.state == "succeeded"
    assert completed.progress_percent == 100
    assert completed.validation_state == "validated"
    assert completed.provider_result_ref == "provider_result:fixture_1"
    assert completed.final_asset_version_ref == "model_asset:validated_v1"


def test_worker_cannot_complete_before_validation_contract_or_with_wrong_provider(monkeypatch, tmp_path):
    game = _game()
    _install(monkeypatch, tmp_path, game)
    monkeypatch.setenv("AURA_GAME_3D_PROVIDER", "fixture3d")
    monkeypatch.setenv("AURA_GAME_3D_PROVIDER_FIXTURE3D_ENABLED", "1")
    body = generation.CreateModelGenerationRequest(capability="text_to_3d", prompt="A small robot.")
    job, _ = generation.create_generation_job(game, "creator_generation", body, correlation_id="corr_create")

    with pytest.raises(ValueError, match="Only running"):
        generation.complete_generation_job(
            game.id,
            job.generation_request_id,
            provider="fixture3d",
            provider_result_ref="result:1",
            final_asset_version_ref="asset:v1",
            correlation_id="corr_early",
        )

    generation.claim_generation_job(game.id, job.generation_request_id, provider="fixture3d", correlation_id="corr_claim")
    with pytest.raises(ValueError, match="provider mismatch"):
        generation.complete_generation_job(
            game.id,
            job.generation_request_id,
            provider="other3d",
            provider_result_ref="result:1",
            final_asset_version_ref="asset:v1",
            correlation_id="corr_wrong",
        )


def test_image_to_3d_requires_project_reference_ids_and_rejects_paths(monkeypatch, tmp_path):
    game = _game()
    _install(monkeypatch, tmp_path, game)
    monkeypatch.setenv("AURA_GAME_3D_PROVIDER", "fixture3d")
    monkeypatch.setenv("AURA_GAME_3D_PROVIDER_FIXTURE3D_ENABLED", "1")

    with pytest.raises(ValueError, match="requires at least one"):
        generation.create_generation_job(
            game,
            "creator_generation",
            generation.CreateModelGenerationRequest(capability="image_to_3d", prompt="Turn this concept into a model."),
            correlation_id="corr_missing_ref",
        )

    with pytest.raises(ValueError, match="opaque identifier"):
        generation.create_generation_job(
            game,
            "creator_generation",
            generation.CreateModelGenerationRequest(
                capability="image_to_3d",
                prompt="Turn this concept into a model.",
                reference_asset_ids=["../../private/reference.png"],
            ),
            correlation_id="corr_path_ref",
        )


def test_failed_provider_job_never_receives_success_references(monkeypatch, tmp_path):
    game = _game()
    _install(monkeypatch, tmp_path, game)
    monkeypatch.setenv("AURA_GAME_3D_PROVIDER", "fixture3d")
    monkeypatch.setenv("AURA_GAME_3D_PROVIDER_FIXTURE3D_ENABLED", "1")
    body = generation.CreateModelGenerationRequest(capability="text_to_3d", prompt="A damaged spaceship hull.")
    job, _ = generation.create_generation_job(game, "creator_generation", body, correlation_id="corr_create")
    generation.claim_generation_job(game.id, job.generation_request_id, provider="fixture3d", correlation_id="corr_claim")
    failed = generation.fail_generation_job(
        game.id,
        job.generation_request_id,
        provider="fixture3d",
        error_code="provider_capacity",
        error_message="Provider capacity unavailable.",
        correlation_id="corr_fail",
    )
    assert failed.state == "failed"
    assert failed.progress_percent is None
    assert failed.provider_result_ref is None
    assert failed.final_asset_version_ref is None
    assert failed.validation_state == "failed"


def test_generation_router_is_composed_into_release_app():
    code = textwrap.dedent(
        """
        import app as production_entrypoint
        paths = production_entrypoint.app.openapi().get("paths", {})
        required = {
            ("get", "/api/game-forge/games/{game_id}/model-generation"),
            ("post", "/api/game-forge/games/{game_id}/model-generation"),
            ("get", "/api/game-forge/games/{game_id}/model-generation/{job_id}"),
            ("delete", "/api/game-forge/games/{game_id}/model-generation/{job_id}"),
        }
        missing = sorted((method, path) for method, path in required if method not in paths.get(path, {}))
        if missing:
            raise SystemExit(f"Game Forge model generation routes missing from production app: {missing}")
        """
    )
    result = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
