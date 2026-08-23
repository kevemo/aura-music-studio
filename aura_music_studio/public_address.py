from __future__ import annotations

import ipaddress
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_STATUS_PATH = "data/public_address_status.json"
DEFAULT_IPV4_DISCOVERY = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except Exception:
        return default


def _safe_ip(value: str | None, *, require_global: bool = False) -> str | None:
    if not value:
        return None
    text = value.strip().strip("[]")
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if require_global and not ip.is_global:
        return None
    return str(ip)


def _is_cgnat(value: str | None) -> bool:
    if not value:
        return False
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10")


def _is_private_or_non_global(value: str | None) -> bool:
    if not value:
        return False
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not ip.is_global


def _local_ipv4() -> str | None:
    """Return the preferred LAN IPv4 without sending application data."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("198.51.100.1", 80))
        return _safe_ip(sock.getsockname()[0])
    except OSError:
        try:
            return _safe_ip(socket.gethostbyname(socket.gethostname()))
        except OSError:
            return None
    finally:
        sock.close()


def _http_text(url: str, *, timeout: int = 12, max_bytes: int = 8192) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Public-address network requests must use HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": "ESP-Live-Sound-Studio/0.11"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("Public-address response was unexpectedly large")
        return body.decode("utf-8", errors="replace").strip()


def _upnpc_binary_external_ip() -> tuple[str | None, str | None]:
    binary = shutil.which("upnpc")
    if not binary:
        return None, "upnpc_not_installed"
    try:
        text = subprocess.check_output([binary, "-s"], text=True, stderr=subprocess.STDOUT, timeout=8)
        for line in text.splitlines():
            if "ExternalIPAddress" in line and "=" in line:
                value = _safe_ip(line.split("=", 1)[1].strip())
                if value:
                    return value, None
        return None, "upnpc_external_ip_not_reported"
    except Exception as exc:
        return None, f"upnpc:{type(exc).__name__}: {exc}"


def _upnp_external_ipv4() -> tuple[str | None, str | None]:
    """Read the router-facing address locally through UPnP when possible.

    The Python miniupnpc module is preferred, then the `upnpc` CLI. No port mappings are created
    by this function. Public HTTPS reflector services are only used later if local router discovery
    cannot provide a global address.
    """
    module_error = None
    try:
        import miniupnpc  # type: ignore
        try:
            upnp = miniupnpc.UPnP()
            upnp.discoverdelay = 250
            if upnp.discover() > 0:
                upnp.selectigd()
                value = _safe_ip(upnp.externalipaddress())
                if value:
                    return value, None
            module_error = "python_upnp_gateway_not_found"
        except Exception as exc:
            module_error = f"python_upnp:{type(exc).__name__}: {exc}"
    except Exception:
        module_error = "python_miniupnpc_not_installed"

    value, cli_error = _upnpc_binary_external_ip()
    if value:
        return value, None
    return None, "; ".join(x for x in (module_error, cli_error) if x)


def _http_public_ipv4() -> tuple[str | None, str | None]:
    raw_urls = os.getenv("LSS_PUBLIC_IPV4_DISCOVERY_URLS", "").strip()
    urls = tuple(x.strip() for x in raw_urls.split(",") if x.strip()) or DEFAULT_IPV4_DISCOVERY
    errors: list[str] = []
    for url in urls:
        try:
            value = _safe_ip(_http_text(url), require_global=True)
            if value and isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address):
                return value, None
            errors.append(f"{urllib.parse.urlparse(url).netloc}: invalid IPv4")
        except Exception as exc:
            errors.append(f"{urllib.parse.urlparse(url).netloc}: {type(exc).__name__}")
    return None, "; ".join(errors) if errors else "no_discovery_service"


def _global_ipv6_candidates() -> list[str]:
    values: set[str] = set()
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6)
    except OSError:
        infos = []
    for info in infos:
        value = _safe_ip(info[4][0])
        if not value:
            continue
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            continue
        if ip.is_global:
            values.add(str(ip))
    return sorted(values)


def _resolved_addresses(hostname: str | None) -> list[str]:
    if not hostname:
        return []
    values: set[str] = set()
    try:
        for info in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM):
            value = _safe_ip(info[4][0])
            if value:
                values.add(value)
    except OSError:
        pass
    return sorted(values)


@dataclass
class PublicAddressStatus:
    checked_at: str = field(default_factory=_now)
    provider: str = "none"
    hostname: str | None = None
    recommended_url: str | None = None
    lan_ipv4: str | None = None
    router_external_ipv4: str | None = None
    public_ipv4: str | None = None
    global_ipv6: list[str] = field(default_factory=list)
    dns_addresses: list[str] = field(default_factory=list)
    ddns_updated: bool = False
    ddns_message: str | None = None
    likely_cgnat: bool = False
    public_ports_required: list[int] = field(default_factory=lambda: [80, 443])
    caddy_https_ready: bool = False
    direct_ip_fallback: str | None = None
    warnings: list[str] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)


class PublicAddressManager:
    """Host-independent public-address manager for the self-hosted ESP Studio."""

    def __init__(self, status_path: str | Path | None = None):
        self.status_path = Path(status_path or os.getenv("LSS_PUBLIC_ADDRESS_STATUS", DEFAULT_STATUS_PATH))
        self.status_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def provider(self) -> str:
        value = (os.getenv("LSS_DDNS_PROVIDER") or "none").strip().lower()
        return value if value in {"none", "direct", "freedns", "duckdns"} else "none"

    @property
    def hostname(self) -> str | None:
        configured = (os.getenv("LSS_PUBLIC_HOST") or "").strip().lower().strip(".")
        if configured:
            return configured
        if self.provider == "duckdns":
            sub = (os.getenv("LSS_DUCKDNS_SUBDOMAIN") or "").strip().lower().removesuffix(".duckdns.org")
            return f"{sub}.duckdns.org" if sub else None
        return None

    def _duckdns_update(self, ipv4: str | None, ipv6: str | None) -> tuple[bool, str]:
        token = (os.getenv("LSS_DUCKDNS_TOKEN") or "").strip()
        sub = (os.getenv("LSS_DUCKDNS_SUBDOMAIN") or "").strip().lower().removesuffix(".duckdns.org")
        if not token or not sub:
            return False, "DuckDNS requires LSS_DUCKDNS_SUBDOMAIN and LSS_DUCKDNS_TOKEN"
        query = {"domains": sub, "token": token, "verbose": "true"}
        if ipv4:
            query["ip"] = ipv4
        if ipv6:
            query["ipv6"] = ipv6
        url = "https://www.duckdns.org/update?" + urllib.parse.urlencode(query)
        reply = _http_text(url, timeout=20)
        ok = reply.splitlines()[0].strip().upper() == "OK" if reply else False
        return ok, "DuckDNS update accepted" if ok else "DuckDNS update was not accepted"

    def _freedns_update(self) -> tuple[bool, str]:
        url = (os.getenv("LSS_FREEDNS_UPDATE_URL") or "").strip()
        if not url:
            return False, "FreeDNS requires LSS_FREEDNS_UPDATE_URL"
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"freedns.afraid.org", "sync.afraid.org"}:
            return False, "FreeDNS update URL must be an official HTTPS afraid.org update URL"
        reply = _http_text(url, timeout=20)
        ok = bool(reply) and not reply.lower().startswith(("error", "fail"))
        return ok, "FreeDNS update accepted" if ok else "FreeDNS update was not accepted"

    def update_ddns(self, ipv4: str | None, ipv6: str | None = None) -> tuple[bool, str | None]:
        if self.provider in {"none", "direct"}:
            return False, "DDNS disabled"
        try:
            if self.provider == "duckdns":
                return self._duckdns_update(ipv4, ipv6)
            if self.provider == "freedns":
                return self._freedns_update()
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"
        return False, "Unsupported DDNS provider"

    def _optional_port_forward(self, lan_ipv4: str | None) -> tuple[bool, str | None]:
        if not _bool("LSS_UPNP_PORT_FORWARD", False):
            return False, "disabled"
        if not lan_ipv4:
            return False, "LAN address unavailable"

        try:
            import miniupnpc  # type: ignore
            try:
                upnp = miniupnpc.UPnP()
                upnp.discoverdelay = 250
                if upnp.discover() > 0:
                    upnp.selectigd()
                    for port in (80, 443):
                        upnp.addportmapping(port, "TCP", lan_ipv4, port, "ESP Live Sound Studio", "")
                    return True, "TCP 80/443 mappings requested through Python miniupnpc"
            except Exception:
                pass
        except Exception:
            pass

        binary = shutil.which("upnpc")
        if not binary:
            return False, "No miniupnpc module or upnpc binary is available"
        try:
            for port in (80, 443):
                subprocess.run(
                    [binary, "-e", "ESP Live Sound Studio", "-a", lan_ipv4, str(port), str(port), "TCP"],
                    check=True,
                    timeout=10,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return True, "TCP 80/443 mappings requested through upnpc"
        except Exception as exc:
            return False, f"upnpc:{type(exc).__name__}: {exc}"

    def check(self, *, update_ddns: bool = True) -> PublicAddressStatus:
        status = PublicAddressStatus(provider=self.provider, hostname=self.hostname)
        status.lan_ipv4 = _local_ipv4()

        router_ip, upnp_error = _upnp_external_ipv4() if _bool("LSS_UPNP_DISCOVERY", True) else (None, "disabled")
        status.router_external_ipv4 = router_ip

        public_ip = router_ip if router_ip and ipaddress.ip_address(router_ip).is_global else None
        public_error = None
        if not public_ip and _bool("LSS_PUBLIC_IP_DISCOVERY", True):
            public_ip, public_error = _http_public_ipv4()
        status.public_ipv4 = public_ip
        status.global_ipv6 = _global_ipv6_candidates()

        status.likely_cgnat = bool(
            router_ip
            and (_is_cgnat(router_ip) or _is_private_or_non_global(router_ip))
            and public_ip
            and router_ip != public_ip
        ) or _is_cgnat(router_ip)

        selected_v6 = status.global_ipv6[0] if status.global_ipv6 else None
        if update_ddns:
            status.ddns_updated, status.ddns_message = self.update_ddns(public_ip, selected_v6)
        else:
            status.ddns_message = "DDNS update skipped"

        status.dns_addresses = _resolved_addresses(status.hostname)
        if status.hostname:
            status.recommended_url = f"https://{status.hostname}"
            status.caddy_https_ready = bool(
                (public_ip and public_ip in status.dns_addresses)
                or (selected_v6 and selected_v6 in status.dns_addresses)
            )
        elif public_ip:
            port = _int("LSS_DIRECT_PUBLIC_PORT", 80)
            suffix = "" if port == 80 else f":{port}"
            status.direct_ip_fallback = f"http://{public_ip}{suffix}"
            status.recommended_url = status.direct_ip_fallback

        mapped, mapping_message = self._optional_port_forward(status.lan_ipv4)
        discovery_source = "router_upnp" if public_ip and public_ip == router_ip else "https_reflector"
        status.diagnostics = {
            "upnp_discovery": upnp_error or "available",
            "public_ip_discovery": public_error or discovery_source,
            "upnpc_binary_available": bool(shutil.which("upnpc")),
            "port_forwarding_requested": mapped,
            "port_forwarding": mapping_message,
            "ddns_secret_exposed": False,
            "cloud_required": False,
            "self_hosted": True,
        }

        if status.likely_cgnat:
            status.warnings.append(
                "The router appears to be behind CGNAT or another upstream NAT. Incoming IPv4 may not work even with local port forwarding."
            )
        if not status.hostname and not status.public_ipv4:
            status.warnings.append("No public hostname or global IPv4 address is currently available.")
        if status.hostname and not status.dns_addresses:
            status.warnings.append("The configured public hostname does not currently resolve in DNS.")
        elif status.hostname and public_ip and public_ip not in status.dns_addresses and selected_v6 not in status.dns_addresses:
            status.warnings.append("The hostname currently resolves somewhere other than this Studio host; DNS may still be propagating.")
        if status.hostname and not status.caddy_https_ready:
            status.warnings.append("HTTPS cannot be declared ready until DNS points at this host and ports 80/443 are reachable.")
        if not _bool("LSS_UPNP_PORT_FORWARD", False):
            status.warnings.append("Automatic router port forwarding is disabled; forward TCP 80/443 manually unless your router is configured separately.")

        self.write_status(status)
        return status

    def write_status(self, status: PublicAddressStatus) -> None:
        self.status_path.write_text(json.dumps(asdict(status), indent=2), encoding="utf-8")

    def read_status(self) -> dict:
        if not self.status_path.is_file():
            return {"configured": False, "status_path": str(self.status_path)}
        try:
            value = json.loads(self.status_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"configured": False}
        except Exception as exc:
            return {"configured": False, "error": f"{type(exc).__name__}: {exc}"}

    def serve_forever(self) -> None:
        interval = _int("LSS_DDNS_INTERVAL_SECONDS", 300, minimum=60)
        while True:
            try:
                self.check(update_ddns=True)
            except Exception as exc:
                self.status_path.write_text(json.dumps({
                    "checked_at": _now(),
                    "provider": self.provider,
                    "hostname": self.hostname,
                    "error": f"{type(exc).__name__}: {exc}",
                }, indent=2), encoding="utf-8")
            time.sleep(interval)


def public_address_status() -> dict:
    return PublicAddressManager().read_status()


def main() -> None:
    PublicAddressManager().serve_forever()


if __name__ == "__main__":
    main()
