from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    monthly_price_usd: Decimal
    description: str
    preview_generation: bool
    confirmed_songs_per_day: int | None
    regeneration_until_confirmed: bool
    master_downloads: bool
    wav_downloads: bool
    stem_downloads: bool
    advanced_studio_tools: bool
    approved_voice_tools: bool
    priority_generation: bool
    studio_claims_output_ownership: bool = False

    def public_dict(self) -> dict:
        data = asdict(self)
        data["monthly_price_usd"] = str(self.monthly_price_usd)
        return data


PLANS: dict[str, Plan] = {
    "free": Plan(
        id="free",
        name="Free",
        monthly_price_usd=Decimal("0.00"),
        description="Explore The Live Sound Studio, create projects and generate real-audio previews.",
        preview_generation=True,
        # Free final-song allowance is intentionally conservative; owners can change policy later
        # without altering the billing/auth architecture.
        confirmed_songs_per_day=0,
        regeneration_until_confirmed=False,
        master_downloads=False,
        wav_downloads=False,
        stem_downloads=False,
        advanced_studio_tools=False,
        approved_voice_tools=False,
        priority_generation=False,
    ),
    "base": Plan(
        id="base",
        name="Base",
        monthly_price_usd=Decimal("4.99"),
        description="One confirmed song per day, with regenerations allowed until that song is accepted.",
        preview_generation=True,
        confirmed_songs_per_day=1,
        regeneration_until_confirmed=True,
        master_downloads=True,
        wav_downloads=True,
        stem_downloads=False,
        advanced_studio_tools=False,
        approved_voice_tools=False,
        priority_generation=False,
    ),
    "pro": Plan(
        id="pro",
        name="Pro",
        monthly_price_usd=Decimal("9.99"),
        description="Unlimited studio use, downloads and the complete Live Sound Studio production toolset.",
        preview_generation=True,
        confirmed_songs_per_day=None,
        regeneration_until_confirmed=True,
        master_downloads=True,
        wav_downloads=True,
        stem_downloads=True,
        advanced_studio_tools=True,
        approved_voice_tools=True,
        priority_generation=True,
    ),
}


def get_plan(plan_id: str) -> Plan:
    key = (plan_id or "").strip().lower()
    if key not in PLANS:
        raise ValueError(f"Unknown plan: {plan_id}")
    return PLANS[key]


def public_plans() -> list[dict]:
    return [PLANS[k].public_dict() for k in ("free", "base", "pro")]


OWNERSHIP_NOTICE = (
    "The Live Sound Studio does not claim ownership of a member's original inputs or eligible "
    "outputs. Rights in AI-assisted outputs can depend on applicable law, source-material rights "
    "and licenses of any underlying open models. Members are responsible for having the rights "
    "to material they upload or ask the Studio to transform."
)
