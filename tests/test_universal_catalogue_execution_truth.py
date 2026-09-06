from types import SimpleNamespace

from aura_music_studio.creative_catalogue import get_catalogue_item
from aura_music_studio.universal_creative_catalogue_api import (
    _item_backend_executable,
    _runtime_row,
)


def _fake_item(*, runtime: str, status: str):
    payload = {
        "id": "future.fx.metadata-only",
        "runtime": runtime,
        "status": status,
        "entitlement": "core",
        "ccc_price": 0,
    }
    return SimpleNamespace(**payload, public=lambda: dict(payload))


def test_current_ffmpeg_audio_effect_is_executable_only_with_functional_status():
    item = get_catalogue_item("music.fx.gain")
    assert item.runtime == "ffmpeg_audio"
    assert _item_backend_executable(item) is True
    row = _runtime_row(item, owned=True)
    assert row["backend_executable"] is True
    assert row["preview_compile_available"] is True
    assert row["execution_truth_contract"] == "allowlisted_runtime_and_lifecycle_status_v1"


def test_future_metadata_runtime_cannot_be_promoted_by_mature_status_alone():
    item = _fake_item(runtime="future_renderer", status="PRODUCTION_VERIFIED")
    assert _item_backend_executable(item) is False
    row = _runtime_row(item, owned=True)
    assert row["backend_executable"] is False
    assert row["preview_compile_available"] is False


def test_known_runtime_cannot_be_promoted_before_functional_lifecycle_status():
    item = _fake_item(runtime="ffmpeg_audio", status="CONTRACT_READY")
    assert _item_backend_executable(item) is False
    row = _runtime_row(item, owned=False)
    assert row["backend_executable"] is False
    assert row["preview_compile_available"] is False
