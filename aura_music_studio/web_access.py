from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests


DEFAULT_TIMEOUT = 25
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
USER_AGENT = "ESP-Live-Sound-Studio-Aura/0.6 (+https://github.com/kevemo/aura-music-studio)"


@dataclass
class WebResult:
    url: str
    status_code: int
    content_type: str
    text: str
    cached: bool = False


class AuraWebGateway:
    """Controlled outbound Internet access for Aura.

    The studio never needs a paid web/search API just to fetch public HTTPS resources.
    Search can use an optional self-hosted SearXNG-compatible endpoint. Direct fetches are
    rate-limited, cached and restricted to http/https. Deployment hosts still need ordinary
    outbound network access; software cannot create Internet connectivity where the host blocks it.
    """

    def __init__(self, cache_dir: str | Path | None = None):
        self.enabled = os.getenv("AURA_WEB_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
        self.allow_http = os.getenv("AURA_WEB_ALLOW_HTTP", "false").lower() in {"1", "true", "yes", "on"}
        self.timeout = int(os.getenv("AURA_WEB_TIMEOUT", str(DEFAULT_TIMEOUT)))
        self.max_bytes = int(os.getenv("AURA_WEB_MAX_BYTES", str(DEFAULT_MAX_BYTES)))
        self.min_interval = float(os.getenv("AURA_WEB_MIN_INTERVAL", "0.5"))
        self.cache_ttl = int(os.getenv("AURA_WEB_CACHE_TTL", "3600"))
        self.searxng_url = (os.getenv("AURA_SEARXNG_URL") or "").rstrip("/")
        self.cache_dir = Path(cache_dir or os.getenv("AURA_WEB_CACHE_DIR", "data/web_cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_request = 0.0

        blocked = os.getenv("AURA_WEB_BLOCKED_DOMAINS", "localhost,127.0.0.1,0.0.0.0,::1")
        self.blocked_domains = {x.strip().lower() for x in blocked.split(",") if x.strip()}
        allowed = os.getenv("AURA_WEB_ALLOWED_DOMAINS", "")
        self.allowed_domains = {x.strip().lower() for x in allowed.split(",") if x.strip()}

    def _validate_url(self, url: str) -> None:
        if not self.enabled:
            raise PermissionError("Aura web access is disabled")
        parsed = urlparse(url)
        if parsed.scheme not in ({"https", "http"} if self.allow_http else {"https"}):
            raise ValueError("Only approved HTTP(S) web access is supported")
        host = (parsed.hostname or "").lower()
        if not host:
            raise ValueError("URL has no hostname")
        if host in self.blocked_domains or any(host.endswith("." + x) for x in self.blocked_domains):
            raise PermissionError("Local/private host access is blocked by the Aura Web Gateway")
        if self.allowed_domains and host not in self.allowed_domains and not any(host.endswith("." + x) for x in self.allowed_domains):
            raise PermissionError("Domain is not in AURA_WEB_ALLOWED_DOMAINS")

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
                url=url,
                status_code=int(meta.get("status_code", 200)),
                content_type=str(meta.get("content_type", "text/plain")),
                text=body_path.read_text(encoding="utf-8", errors="replace"),
                cached=True,
            )
        except Exception:
            return None

    def _write_cache(self, result: WebResult) -> None:
        meta_path, body_path = self._cache_paths(result.url)
        meta_path.write_text(json.dumps({
            "url": result.url,
            "status_code": result.status_code,
            "content_type": result.content_type,
            "stored_at": time.time(),
        }, indent=2), encoding="utf-8")
        body_path.write_text(result.text, encoding="utf-8")

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.time()

    def fetch_text(self, url: str, *, use_cache: bool = True) -> WebResult:
        self._validate_url(url)
        if use_cache:
            cached = self._read_cache(url)
            if cached:
                return cached
        self._throttle()
        with requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.5"},
            timeout=self.timeout,
            stream=True,
            allow_redirects=True,
        ) as response:
            response.raise_for_status()
            chunks = []
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
            text = raw.decode(encoding, errors="replace")
            result = WebResult(
                url=str(response.url),
                status_code=response.status_code,
                content_type=response.headers.get("content-type", ""),
                text=text,
                cached=False,
            )
        if use_cache:
            self._write_cache(result)
        return result

    def search(self, query: str, *, limit: int = 10) -> list[dict]:
        """Search via a self-hosted SearXNG-compatible instance when configured.

        This avoids requiring a paid search API. A public deployment should run its own
        SearXNG instance or another approved metasearch service rather than scraping search
        result pages directly.
        """
        if not self.searxng_url:
            raise RuntimeError(
                "No search backend configured. Set AURA_SEARXNG_URL to a self-hosted SearXNG instance. "
                "Aura can still fetch known public URLs directly without it."
            )
        endpoint = f"{self.searxng_url}/search"
        self._validate_url(endpoint)
        self._throttle()
        response = requests.get(
            endpoint,
            params={"q": query, "format": "json", "language": "all", "safesearch": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        results = []
        for item in payload.get("results", [])[: max(1, min(int(limit), 25))]:
            url = item.get("url")
            if not url:
                continue
            results.append({
                "title": re.sub(r"\s+", " ", str(item.get("title") or "")).strip(),
                "url": url,
                "content": re.sub(r"\s+", " ", str(item.get("content") or "")).strip(),
                "engine": item.get("engine"),
            })
        return results

    def diagnostics(self) -> dict:
        return {
            "enabled": self.enabled,
            "direct_https_fetch": True,
            "allow_http": self.allow_http,
            "self_hosted_search_configured": bool(self.searxng_url),
            "searxng_url": self.searxng_url or None,
            "cache_dir": str(self.cache_dir),
            "cache_ttl_seconds": self.cache_ttl,
            "max_response_bytes": self.max_bytes,
            "host_must_allow_outbound_egress": True,
        }
