from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path


VALID_PROVIDERS = {"none", "direct", "freedns", "duckdns"}
KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.I)
DUCK_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.I)


@dataclass
class SelfHostInitResult:
    env_path: str
    provider: str
    hostname: str | None
    public_site_address: str
    public_base_url: str
    secure_cookie: bool
    admin_key_generated: bool
    admin_key: str | None
    provenance_secret_generated: bool
    missing_private_settings: list[str]
    next_command: str


def _read_template(template_path: Path) -> list[str]:
    if template_path.is_file():
        return template_path.read_text(encoding="utf-8").splitlines()
    return [
        "# ESP Live Sound Studio generated environment",
        "LSS_PUBLIC_BASE_URL=auto",
        "LSS_COOKIE_SECURE=false",
        "LSS_ADMIN_KEY=",
        "LSS_PROVENANCE_SECRET=",
        "LSS_DDNS_PROVIDER=none",
        "LSS_PUBLIC_HOST=",
        "LSS_PUBLIC_SITE_ADDRESS=http://:80",
        "LSS_DUCKDNS_SUBDOMAIN=",
        "LSS_DUCKDNS_TOKEN=",
        "LSS_FREEDNS_UPDATE_URL=",
    ]


def _values(lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in lines:
        match = KEY_RE.match(line.strip())
        if match:
            result[match.group(1)] = match.group(2)
    return result


def _set(lines: list[str], key: str, value: str) -> list[str]:
    prefix = key + "="
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = prefix + value
            return lines
    lines.append(prefix + value)
    return lines


def _hostname(value: str | None) -> str | None:
    text = (value or "").strip().lower().strip(".")
    if not text:
        return None
    if not HOST_RE.fullmatch(text):
        raise ValueError("Public hostname is not a valid DNS hostname")
    return text


def initialize_self_host(
    *,
    provider: str = "direct",
    hostname: str | None = None,
    duckdns_subdomain: str | None = None,
    env_path: str | Path = ".env",
    template_path: str | Path = ".env.example",
) -> SelfHostInitResult:
    """Prepare a secure self-host `.env` without collecting DDNS/payment/email secrets on CLI.

    Existing .env values are preserved unless they are settings this initializer owns. Owner and
    provenance secrets are generated only when their current values are blank.
    """
    provider = (provider or "direct").strip().lower()
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"Provider must be one of: {', '.join(sorted(VALID_PROVIDERS))}")

    env_path = Path(env_path)
    template_path = Path(template_path)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else _read_template(template_path)
    current = _values(lines)

    duck_sub = (duckdns_subdomain or current.get("LSS_DUCKDNS_SUBDOMAIN") or "").strip().lower().removesuffix(".duckdns.org")
    if provider == "duckdns":
        if not duck_sub or not DUCK_RE.fullmatch(duck_sub):
            raise ValueError("DuckDNS mode requires a valid --duckdns-subdomain")
        resolved_host = _hostname(hostname) or f"{duck_sub}.duckdns.org"
    else:
        resolved_host = _hostname(hostname or current.get("LSS_PUBLIC_HOST")) if provider == "freedns" else _hostname(hostname)

    if provider == "freedns" and not resolved_host:
        raise ValueError("FreeDNS mode requires --hostname for the selected free DNS record")

    admin_key = (current.get("LSS_ADMIN_KEY") or "").strip()
    admin_generated = not bool(admin_key)
    if admin_generated:
        admin_key = secrets.token_urlsafe(36)

    provenance = (current.get("LSS_PROVENANCE_SECRET") or "").strip()
    provenance_generated = not bool(provenance)
    if provenance_generated:
        provenance = secrets.token_urlsafe(48)

    secure_cookie = bool(resolved_host and provider in {"freedns", "duckdns"})
    site_address = resolved_host if secure_cookie else "http://:80"

    owned = {
        "LSS_PUBLIC_BASE_URL": "auto",
        "LSS_COOKIE_SECURE": "true" if secure_cookie else "false",
        "LSS_DDNS_PROVIDER": provider,
        "LSS_PUBLIC_HOST": resolved_host or "",
        "LSS_PUBLIC_SITE_ADDRESS": site_address,
        "LSS_ADMIN_KEY": admin_key,
        "LSS_PROVENANCE_SECRET": provenance,
    }
    if provider == "duckdns":
        owned["LSS_DUCKDNS_SUBDOMAIN"] = duck_sub
    for key, value in owned.items():
        _set(lines, key, value)

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass

    latest = _values(lines)
    missing: list[str] = []
    if provider == "duckdns" and not (latest.get("LSS_DUCKDNS_TOKEN") or "").strip():
        missing.append("LSS_DUCKDNS_TOKEN")
    if provider == "freedns" and not (latest.get("LSS_FREEDNS_UPDATE_URL") or "").strip():
        missing.append("LSS_FREEDNS_UPDATE_URL")
    if not (latest.get("LSS_SMTP_USERNAME") or "").strip() or not (latest.get("LSS_SMTP_PASSWORD") or "").strip():
        missing.append("ESP SMTP credentials (only required for real approval-email delivery)")

    return SelfHostInitResult(
        env_path=str(env_path),
        provider=provider,
        hostname=resolved_host,
        public_site_address=site_address,
        public_base_url="auto",
        secure_cookie=secure_cookie,
        admin_key_generated=admin_generated,
        admin_key=admin_key if admin_generated else None,
        provenance_secret_generated=provenance_generated,
        missing_private_settings=missing,
        next_command="docker compose --profile public up -d --build" if provider != "none" else "docker compose up -d --build",
    )
