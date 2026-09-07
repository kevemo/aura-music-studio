from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import aura_music_studio.provider_budget_enforcement as budget
import aura_music_studio.provider_cost_governance as governance
from aura_music_studio.provider_cost_governance import ProviderCostStore


def _store(monkeypatch, tmp_path: Path) -> ProviderCostStore:
    store = ProviderCostStore(tmp_path / "provider-budget.sqlite3")
    monkeypatch.setattr(governance, "store", store)
    return store


def test_warning_mode_preserves_non_blocking_submission_contract(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.delenv("AURA_PROVIDER_COST_ENFORCEMENT", raising=False)
    monkeypatch.delenv("AURA_PROVIDER_COST_ESTIMATE_COMFYUI_VIDEO_MINOR", raising=False)
    monkeypatch.delenv("AURA_PROVIDER_COST_BUDGET_DAILY_MINOR", raising=False)

    assert budget.reserve_provider_budget(provider="comfyui", service="video", operation="render") is None


def test_hard_mode_fails_closed_when_provider_work_is_unpriced(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv("AURA_PROVIDER_COST_ENFORCEMENT", "hard")
    monkeypatch.setenv("AURA_PROVIDER_COST_BUDGET_DAILY_MINOR", "100")
    monkeypatch.delenv("AURA_PROVIDER_COST_ESTIMATE_COMFYUI_VIDEO_RENDER_MINOR", raising=False)
    monkeypatch.delenv("AURA_PROVIDER_COST_ESTIMATE_COMFYUI_VIDEO_MINOR", raising=False)
    monkeypatch.delenv("AURA_PROVIDER_COST_ESTIMATE_COMFYUI_MINOR", raising=False)

    with pytest.raises(budget.ProviderBudgetExceeded, match="cost estimate"):
        budget.reserve_provider_budget(provider="comfyui", service="video", operation="render")


def test_hard_mode_requires_at_least_one_operator_budget(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv("AURA_PROVIDER_COST_ENFORCEMENT", "hard")
    monkeypatch.setenv("AURA_PROVIDER_COST_ESTIMATE_COMFYUI_VIDEO_RENDER_MINOR", "25")
    monkeypatch.delenv("AURA_PROVIDER_COST_BUDGET_DAILY_MINOR", raising=False)
    monkeypatch.delenv("AURA_PROVIDER_COST_BUDGET_MONTHLY_MINOR", raising=False)

    with pytest.raises(budget.ProviderBudgetExceeded, match="daily or monthly budget"):
        budget.reserve_provider_budget(provider="comfyui", service="video", operation="render")


def test_atomic_reservation_prevents_concurrent_budget_oversubscription(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    monkeypatch.setenv("AURA_PROVIDER_COST_ENFORCEMENT", "hard")
    monkeypatch.setenv("AURA_PROVIDER_COST_ESTIMATE_COMFYUI_VIDEO_RENDER_MINOR", "60")
    monkeypatch.setenv("AURA_PROVIDER_COST_BUDGET_DAILY_MINOR", "100")
    monkeypatch.delenv("AURA_PROVIDER_COST_BUDGET_MONTHLY_MINOR", raising=False)

    first = budget.reserve_provider_budget(provider="comfyui", service="video", operation="render")
    assert first

    with pytest.raises(budget.ProviderBudgetExceeded, match="daily hard budget"):
        budget.reserve_provider_budget(provider="comfyui", service="video", operation="render")

    budget.release_provider_budget(first)
    second = budget.reserve_provider_budget(provider="comfyui", service="video", operation="render")
    assert second and second != first
    budget.release_provider_budget(second)

    with sqlite3.connect(store.db_path) as con:
        remaining = con.execute("SELECT COUNT(*) FROM provider_cost_reservations").fetchone()[0]
    assert remaining == 0


def test_committed_spend_is_counted_before_new_reservation(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    monkeypatch.setenv("AURA_PROVIDER_COST_ENFORCEMENT", "hard")
    monkeypatch.setenv("AURA_PROVIDER_COST_ESTIMATE_COMFYUI_IMAGE_RENDER_MINOR", "30")
    monkeypatch.setenv("AURA_PROVIDER_COST_BUDGET_DAILY_MINOR", "100")
    monkeypatch.delenv("AURA_PROVIDER_COST_BUDGET_MONTHLY_MINOR", raising=False)
    store.record_submission(
        provider="comfyui",
        service="image",
        operation="render",
        job_ref="existing-paid-job",
        estimated_cost_minor=80,
    )

    with pytest.raises(budget.ProviderBudgetExceeded, match="daily hard budget"):
        budget.reserve_provider_budget(provider="comfyui", service="image", operation="render")


def test_invalid_enforcement_mode_fails_configuration_closed(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv("AURA_PROVIDER_COST_ENFORCEMENT", "sometimes")

    with pytest.raises(RuntimeError, match="warning.*hard"):
        budget.reserve_provider_budget(provider="comfyui", service="image", operation="render")


def test_package_installs_budget_boundary_without_changing_commercial_authority():
    package_source = Path("aura_music_studio/__init__.py").read_text(encoding="utf-8")
    guard_source = Path("aura_music_studio/provider_budget_enforcement.py").read_text(encoding="utf-8")

    assert "install_provider_budget_enforcement()" in package_source
    assert "Creation Coins" not in guard_source
    assert "subscription" not in guard_source.lower()
    assert "owner_session" not in guard_source
    assert "role" not in guard_source.lower()
    assert "shell" not in guard_source.lower()
