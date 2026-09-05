from __future__ import annotations

import ssl
from collections.abc import Iterable, Mapping
from urllib.parse import urlparse

import requests
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.exceptions import HTTPError as Urllib3HTTPError


def validate_proxy_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("AURA_WEB_EGRESS_PROXY is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("AURA_WEB_EGRESS_PROXY must be an HTTP(S) proxy URL")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("AURA_WEB_EGRESS_PROXY must not contain a path, query or fragment")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("AURA_WEB_EGRESS_PROXY port is invalid")
    return raw.rstrip("/")


def _host_header(host: str, port: int, scheme: str) -> str:
    display = f"[{host}]" if ":" in host else host
    default = 443 if scheme == "https" else 80
    return display if port == default else f"{display}:{port}"


def _request_target(url: str) -> str:
    parsed = urlparse(url)
    target = parsed.path or "/"
    if parsed.params:
        target += ";" + parsed.params
    if parsed.query:
        target += "?" + parsed.query
    return target


def _as_requests_response(raw, *, url: str) -> requests.Response:
    response = requests.Response()
    response.status_code = int(raw.status)
    response.headers = requests.structures.CaseInsensitiveDict(raw.headers.items())
    response.raw = raw
    response.url = url
    response.encoding = requests.utils.get_encoding_from_headers(response.headers)
    return response


def pinned_get(
    url: str,
    *,
    addresses: Iterable[str],
    headers: Mapping[str, str],
    timeout: int | float,
) -> requests.Response:
    """GET a public URL by connecting only to a caller-validated numeric address.

    The original hostname is retained for TLS SNI/certificate verification and the Host header.
    No target-host DNS lookup is performed by this layer after the caller supplies ``addresses``.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Pinned transport supports only HTTP(S)")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("Pinned transport requires a hostname")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("Pinned transport URL port is invalid") from exc

    candidates = tuple(dict.fromkeys(str(address) for address in addresses if str(address)))
    if not candidates:
        raise ValueError("Pinned transport requires at least one validated address")

    request_headers = dict(headers)
    request_headers["Host"] = _host_header(host, port, parsed.scheme)
    target = _request_target(url)
    last_error: BaseException | None = None

    for address in candidates:
        connection = None
        try:
            if parsed.scheme == "https":
                context = ssl.create_default_context(cafile=requests.certs.where())
                connection = HTTPSConnection(
                    address,
                    port,
                    timeout=timeout,
                    ssl_context=context,
                    server_hostname=host,
                    assert_hostname=host,
                )
            else:
                connection = HTTPConnection(address, port, timeout=timeout)
            connection.request(
                "GET",
                target,
                headers=request_headers,
                preload_content=False,
                decode_content=True,
            )
            raw = connection.getresponse()
            return _as_requests_response(raw, url=url)
        except (OSError, ssl.SSLError, Urllib3HTTPError) as exc:
            last_error = exc
            if connection is not None:
                connection.close()

    raise requests.ConnectionError("Unable to connect to any validated web address") from last_error


def explicit_proxy_get(
    url: str,
    *,
    proxy_url: str,
    headers: Mapping[str, str],
    timeout: int | float,
) -> requests.Response:
    """GET through one explicitly configured proxy without trusting ambient proxy variables."""
    proxy = validate_proxy_url(proxy_url)
    if not proxy:
        raise ValueError("Explicit egress proxy is not configured")
    session = requests.Session()
    session.trust_env = False
    try:
        return session.get(
            url,
            headers=dict(headers),
            timeout=timeout,
            stream=True,
            allow_redirects=False,
            proxies={"http": proxy, "https": proxy},
        )
    except Exception:
        session.close()
        raise


def no_env_session() -> requests.Session:
    """Create a requests session that ignores ambient HTTP(S)_PROXY/NO_PROXY variables."""
    session = requests.Session()
    session.trust_env = False
    return session


__all__ = ["explicit_proxy_get", "no_env_session", "pinned_get", "validate_proxy_url"]
