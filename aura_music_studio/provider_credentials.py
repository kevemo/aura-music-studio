from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class CredentialSpec:
    env_name: str
    provider: str
    purpose: str
    required_for: tuple[str, ...] = ()
    secret: bool = True
    optional: bool = True

    def status(self) -> dict:
        value = os.getenv(self.env_name, "")
        configured = bool(value.strip())
        return {
            **asdict(self),
            "configured": configured,
            # Never return values, prefixes, lengths or hashes. Even metadata about secrets
            # can become an unnecessary side channel in public diagnostics.
            "value": None,
        }


CREDENTIALS: tuple[CredentialSpec, ...] = (
    CredentialSpec("LSS_ADMIN_KEY", "Live Sound Studio", "Owner/admin portal authentication", ("owner portal",), optional=False),
    CredentialSpec("LSS_PROVENANCE_SECRET", "Live Sound Studio", "Signs provenance/integrity records", ("production provenance",), optional=False),
    CredentialSpec("LSS_SMTP_USERNAME", "SMTP/Gmail", "Membership and ESP approval email sender", ("email notifications",), secret=False),
    CredentialSpec("LSS_SMTP_PASSWORD", "SMTP/Gmail", "App-specific SMTP credential", ("email notifications",)),
    CredentialSpec("OPENAI_API_KEY", "OpenAI", "Aura Companion, GPT Image and OpenAI video providers", ("Aura cloud reasoning", "image generation", "video generation")),
    CredentialSpec("RUNWAYML_API_SECRET", "Runway", "Runway video generation provider", ("video generation",)),
    CredentialSpec("DEAPI_API_KEY", "deAPI", "Hosted ACE-Step music generation fallback", ("music generation",)),
    CredentialSpec("ELEVENLABS_API_KEY", "ElevenLabs", "Hosted audio/music/stem and voice-capable integrations where enabled", ("audio cloud fallback",)),
    CredentialSpec("MUREKA_API_KEY", "Mureka", "Hosted music generation provider", ("music generation",)),
    CredentialSpec("HF_TOKEN", "Hugging Face", "Authenticated model/download access where licence permits", ("model provisioning",)),
    CredentialSpec("ACESTEP_API_KEY", "ACE-Step", "Optional authentication for the Studio's ACE-Step service", ("local/remote ACE-Step",)),
    CredentialSpec("AURA_LLM_API_KEY", "OpenAI-compatible LLM", "Optional private/local OpenAI-compatible reasoning endpoint", ("Aura reasoning",)),
    CredentialSpec("AURA_PRODUCER_LLM_KEY", "External producer planner", "Optional producer-planner authentication", ("Aura producer fallback",)),
)


def credential_report(specs: Iterable[CredentialSpec] = CREDENTIALS) -> dict:
    items = [spec.status() for spec in specs]
    required = [item for item in items if not item["optional"]]
    return {
        "credentials": items,
        "summary": {
            "total": len(items),
            "configured": sum(1 for item in items if item["configured"]),
            "missing": sum(1 for item in items if not item["configured"]),
            "required_missing": [item["env_name"] for item in required if not item["configured"]],
        },
        "security": {
            "secret_values_returned": False,
            "repository_secret_storage": False,
            "client_side_secret_storage": False,
            "recommended_storage": "deployment/environment secret manager",
        },
    }


def provider_ready(provider: str) -> bool:
    provider = (provider or "").strip().lower()
    matching = [spec for spec in CREDENTIALS if spec.provider.lower() == provider]
    return bool(matching) and all(bool(os.getenv(spec.env_name, "").strip()) for spec in matching if not spec.optional)
