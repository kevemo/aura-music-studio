from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .web_transport import explicit_proxy_get, no_env_session, pinned_get, validate_proxy_url


DEFAULT_TIMEOUT = 25
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
USER_AGENT = "ESP-Command-Center-Aura/1.0"


@dataclass
class WebResult:
    url: str
    status_code: int
    content_type: str
    text: str
    cached: bool = False


class AuraWebGateway:
    """Controlled outbound Internet access for Aura.

    Direct public fetches resolve and validate every target, then connect to one of those exact
    validated numeric addresses while retaining the original hostname for TLS and HTTP Host.
    This closes the validation-to-connect DNS-rebinding window. An explicitly configured egress
    proxy is a separate trust boundary: target-DNS enforcement may be delegated only when the
    operator explicitly enables that trust. Ambient HTTP(S)_PROXY variables are never authority.

    Search may use an explicitly configured self-hosted SearXNG service on the Command Center's
    private network. That exception applies only to the exact configured search service, never to
    arbitrary member-requested URLs.
    """

    def __init__(self, cache_dir: str | Path | None = None):
        self.enabled = os.getenv("AURA_WEB_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
        self.allow_http = os.getenv("AURA_WEB_ALLOW_HTTP", "false").lower() in {"1", "true", "yes", "on"}
        self.timeout = int(os.getenv("AURA_WEB_TIMEOUT", str(DEFAULT_TIMEOUT)))
        self.max_bytes = int(os.getenv("AURA_WEB_MAX_BYTES", str(DEFAULT_MAX_BYTES)))
        self.min_interval = float(os.getenv("AURA_WEB_MIN_INTERVAL", "0.5"))
        self.cache_ttl = int(os.getenv("AURA_WEB_CACHE_TTL", "3600"))
        self.searxng_url = (os.getenv("AURA_SEARXNG_URL") or "").rstrip("/")
        self.egress_proxy = validate_proxy_url(os.getenv("AURA_WEB_EGRESS_PROXY") or "")
        self.trust_egress_proxy_dns = (os.getenv("AURA_WEB_TRUST_EGRESS_PROXY_DNS") or "false").lower() in {
            "1", "true", "yes", "on"
        }
        self.cache_dir = Path(cache_dir or os.getenv("AURA_WEB_CACHE_DIR", "data/web_cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_request = 0.0

        blocked = os.getenv("AURA_WEB_BLOCKED_DOMAINS", "localhost,127.0.0.1,0.0.0.0,::1")
        self.blocked_domains = {x.strip().lower().rstrip(".") for x in blocked.split(",") if x.strip()}
        allowed = os.getenv("AURA_WEB_ALLOWED_DOMAINS", "")
        self.allowed_domains = {x.strip().lower().rstrip(".") for x in allowed.split(",") if x.strip()}

    @staticmethod
    def _is_public_ip(value: str) -> bool:
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            return False
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )

    @staticmethod
    def _port(parsed) -> int:
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise ValueError("URL port is invalid") from exc
        if not 1 <= port <= 65535:
            raise ValueError("URL port is invalid")
        return port

    def _is_configured_search_url(self, url: str) -> bool:
        if not self.searxng_url:
            return False
        base = urlparse(self.searxng_url)
        current = urlparse(url)
        try:
            base_port = self._port(base)
            current_port = self._port(current)
        except ValueError:
            return False
        return (
            current.scheme == base.scheme
            and (current.hostname or "").lower().rstrip(".") == (base.hostname or "").lower().rstrip(".")
            and current_port == base_port
        )

    def _resolve_public_addresses(self, host: str, port: int) -> tuple[str, ...]:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"Unable to resolve web host: {host}") from exc
        addresses = tuple(dict.fromkeys(info[4][0] for info in infos if info and info[4]))
        if not addresses:
            raise ValueError(f"Unable to resolve web host: {host}")
        if any(not self._is_public_ip(address) for address in addresses):
            raise PermissionError("Resolved private/local network addresses are blocked")
        return addresses

    def _validate_url(
        self,
        url: str,
        *,
        allow_configured_search: bool = False,
    ) -> tuple[str, ...] | None:
        if not self.enabled:
            raise PermissionError("Aura web access is disabled")
        parsed = urlparse(url)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Web URLs may not contain embedded credentials")
        internal_search = allow_configured_search and self._is_configured_search_url(url)
        permitted_schemes = {"https", "http"} if self.allow_http or internal_search else {"https"}
        if parsed.scheme not in permitted_schemes:
            raise ValueError("Only approved HTTP(S) web access is supported")
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            raise ValueError("URL has no hostname")
        port = self._port(parsed)

        if internal_search:
            return None
        if host in self.blocked_domains or any(host.endswith("." + x) for x in self.blocked_domains):
            raise PermissionError("Local/private host access is blocked by the Aura Web Gateway")
        if self.allowed_domains and host not in self.allowed_domains and not any(
            host.endswith("." + x) for x in self.allowed_domains
        ):
            raise PermissionError("Domain is not in AURA_WEB_ALLOWED_DOMAINS")
        return self._resolve_public_addresses(host, port)

    @staticmethod
    def _cache_key(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        key = self._cache_key(url)
        return self.cache_dir / f"{key}.json", self.cache_dir / f"{key}.txt"

    def _read_cache(self, url: str) -> WebResult | None:
        meta_path, body_path = self._cache_paths(url)
        if not meta_path.exists() or not body_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if time.time() - float(meta.get("stored_at", 0)) > self.cache_ttl:
                return None
            return WebResult(
                url=str(meta.get("final_url") or url),
                status_code=int(meta.get("status_code", 200)),
                content_type=str(meta.get("content_type", "text/plain")),
                text=body_path.read_text(encoding="utf-8", errors="replace"),
                cached=True,
            )
        except Exception:
            return None

    def _write_cache(self, requested_url: str, result: WebResult) -> None:
        meta_path, body_path = self._cache_paths(requested_url)
        meta_path.write_text(
            json.dumps(
                {
                    "requested_url": requested_url,
                    "final_url": result.url,
                    "status_code": result.status_code,
                    "content_type": result.content_type,
                    "stored_at": time.time(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        body_path.write_text(result.text, encoding="utf-8")

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.time()

    @staticmethod
    def _public_headers() -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.5",
        }

    def _public_get(self, url: str, addresses: tuple[str, ...]):
        if self.egress_proxy:
            if not self.trust_egress_proxy_dns:
                raise PermissionError(
                    "AURA_WEB_EGRESS_PROXY is configured but target-DNS enforcement has not been explicitly delegated"
                )
            return explicit_proxy_get(
                url,
                proxy_url=self.egress_proxy,
                headers=self._public_headers(),
                timeout=self.timeout,
            )
        return pinned_get(
            url,
            addresses=addresses,
            headers=self._public_headers(),
            timeout=self.timeout,
        )

    def _request_with_safe_redirects(self, url: str):
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            addresses = self._validate_url(current)
            if not addresses:
                raise PermissionError("Public fetch did not produce a validated address set")
            self._throttle()
            response = self._public_get(current, addresses)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                response.close()
                if not location:
                    raise ValueError("Redirect response did not include a Location header")
                current = urljoin(current, location)
                continue
            return response
        raise ValueError("Web request exceeded maximum redirects")

    def fetch_text(self, url: str, *, use_cache: bool = True) -> WebResult:
        # Validate policy before a cache read so a later allow/block-list change applies to cached
        # material too. A live request validates again immediately before selecting its pinned IP.
        self._validate_url(url)
        if use_cache:
            cached = self._read_cache(url)
            if cached:
                return cached

        response = self._request_with_safe_redirects(url)
        with response:
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > self.max_bytes:
                    raise ValueError("Web response exceeded AURA_WEB_MAX_BYTES")
                chunks.append(chunk)
            raw = b"".join(chunks)
            encoding = response.encoding or "utf-8"
            result = WebResult(
                url=str(response.url),
                status_code=response.status_code,
                content_type=response.headers.get("content-type", ""),
                text=raw.decode(encoding, errors="replace"),
                cached=False,
            )
        if use_cache:
            self._write_cache(url, result)
        return result

    def search(self, query: str, *, limit: int = 10) -> list[dict]:
        """Search through the explicitly configured self-hosted SearXNG JSON endpoint."""
        query = re.sub(r"\s+", " ", (query or "")).strip()
        if not query:
            raise ValueError("Search query is empty")
        if not self.searxng_url:
            raise RuntimeError(
                "No search backend configured. Set AURA_SEARXNG_URL to the Command Center's SearXNG service. "
                "Aura can still fetch known public HTTPS URLs directly without search."
            )
        endpoint = f"{self.searxng_url}/search"
        self._validate_url(endpoint, allow_configured_search=True)
        self._throttle()
        with no_env_session() as session:
            response = session.get(
                endpoint,
                params={"q": query, "format": "json", "language": "all", "safesearch": 1},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=self.timeout,
                allow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
        results: list[dict] = []
        for item in payload.get("results", [])[: max(1, min(int(limit), 25))]:
            result_url = item.get("url")
            if not result_url:
                continue
            try:
                self._validate_url(result_url)
            except Exception:
                continue
            results.append(
                {
                    "title": re.sub(r"\s+", " ", str(item.get("title") or "")).strip(),
                    "url": result_url,
                    "content": re.sub(r"\s+", " ", str(item.get("content") or "")).strip(),
                    "engine": item.get("engine"),
                }
            )
        return results

    def diagnostics(self) -> dict:
        if self.egress_proxy:
            rebinding = "delegated_to_explicit_egress_proxy" if self.trust_egress_proxy_dns else "proxy_untrusted_fail_closed"
        else:
            rebinding = "direct_validated_ip_pinning"
        return {
            "enabled": self.enabled,
            "direct_https_fetch": True,
            "allow_http_for_public_fetch": self.allow_http,
            "private_network_fetch_blocked": True,
            "dns_private_ip_blocking": True,
            "dns_rebinding_protection": rebinding,
            "direct_connection_pins_validated_ip": not bool(self.egress_proxy),
            "ambient_proxy_environment_ignored": True,
            "explicit_egress_proxy_configured": bool(self.egress_proxy),
            "explicit_egress_proxy_dns_trust_enabled": bool(self.egress_proxy and self.trust_egress_proxy_dns),
            "safe_redirect_validation": True,
            "self_hosted_search_configured": bool(self.searxng_url),
            "searxng_url": self.searxng_url or None,
            "cache_ttl_seconds": self.cache_ttl,
            "max_response_bytes": self.max_bytes,
            "host_must_allow_outbound_egress": True,
        }
