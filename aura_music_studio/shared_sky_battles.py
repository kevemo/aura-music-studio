from __future__ import annotations

from .shared_sky_battle_types import (
    MAX_PARTICIPANTS, BattleDomainError, CommittedGiftEvent, EngagementScoreEvent,
    ReversedGiftEvent, iso, parse_time,
)
from .shared_sky_battle_base import BattleStoreBase
from .shared_sky_battle_host_invites import BattleHostInviteMixin
from .shared_sky_battle_join_requests import BattleJoinRequestMixin
from .shared_sky_battle_participant_state import BattleParticipantStateMixin
from .shared_sky_battle_rules import BattleRulesMixin
from .shared_sky_battle_session import BattleSessionMixin
from .shared_sky_battle_score_inputs import BattleScoreInputMixin
from .shared_sky_battle_score_corrections import BattleScoreCorrectionMixin
from .shared_sky_battle_finalization import BattleFinalizationMixin
from .shared_sky_battle_reconciliation import BattleReconciliationMixin
from .shared_sky_battle_snapshots import BattleSnapshotMixin
from .shared_sky_battle_planning import BattlePlanningMixin

class SharedSkyBattleStore(
    BattleHostInviteMixin, BattleJoinRequestMixin, BattleParticipantStateMixin, BattleRulesMixin,
    BattleSessionMixin, BattleScoreInputMixin, BattleScoreCorrectionMixin, BattleFinalizationMixin,
    BattleReconciliationMixin, BattleSnapshotMixin, BattlePlanningMixin, BattleStoreBase,
):
    """Server-authoritative Shared Sky participant and Battle domain.

    Canonical LIVE identity remains ``shared_sky_broadcasts.id``. No Coin wallet, payout,
    viewer-counter, chat or media-packet authority exists in this domain.
    """

__all__ = [
    "MAX_PARTICIPANTS", "BattleDomainError", "CommittedGiftEvent", "ReversedGiftEvent",
    "EngagementScoreEvent", "SharedSkyBattleStore", "iso", "parse_time",
]
