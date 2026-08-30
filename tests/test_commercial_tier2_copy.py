from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura_music_studio.commercial_entitlements import require_media_download


class _NoDownloadPlan:
    def has(self, _feature: str) -> bool:
        return False


def test_music_download_denial_uses_current_tier2_price_and_name():
    member = SimpleNamespace(plan=_NoDownloadPlan())

    with pytest.raises(PermissionError, match=r"£5\.99 Tier 2"):
        require_media_download(member, "audio")
