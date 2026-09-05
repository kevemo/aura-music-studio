from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import socket
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

_MAX_REQUESTS = 500
_MAX_CONCURRENCY = 20
_MAX_TIMEOUT_SECONDS = 15.0
_MAX_P95_THRESHOLD_MS = 60_000.0
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class ProbeConfig:
    url: str
    requests: int = 50
    concurrency: int = 5
    timeout_seconds: float = 5.0
    allow_remote: bool = False
    minimum_success_ratio: float = 1.0
    maximum_p95_ms: float | None = None

    def validated(self) -> "ProbeConfig":
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Load probe target must be an http(s) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Load probe target must not contain embedded credentials")
        if parsed.fragment:
            raise ValueError("Load probe target must not contain a URL fragment")
        remote = parsed.hostname.lower() not in _LOOPBACK_HOSTS
        if remote and not self.allow_remote:
            raise PermissionError("Remote load probes require explicit allow_remote=true")
        if remote and parsed.scheme != "https":
            raise ValueError("Remote load probes require HTTPS")
        if not 1 <= int(self.requests) <= _MAX_REQUESTS:
            raise ValueError(f"requests must be between 1 and {_MAX_REQUESTS}")
        if not 1 <= int(self.concurrency) <= _MAX_CONCURRENCY:
            raise ValueError(f"concurrency must be between 1 and {_MAX_CONCURRENCY}")
        if self.concurrency > self.requests:
            raise ValueError("concurrency cannot exceed request count")
        if not 0.1 <= float(self.timeout_seconds) <= _MAX_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds must be between 0.1 and {_MAX_TIMEOUT_SECONDS}")
        if not 0.0 <= float(self.minimum_success_ratio) <= 1.0:
            raise ValueError("minimum_success_ratio must be between 0.0 and 1.0")
        if self.maximum_p95_ms is not None and not 1.0 <= float(self.maximum_p95_ms) <= _MAX_P95_THRESHOLD_MS:
            raise ValueError(f"maximum_p95_ms must be between 1 and {_MAX_P95_THRESHOLD_MS:g}")
        return self


def _one(url: str, timeout: float) -> tuple[int, float, str | None]:
    started = time.perf_counter()
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "AuraProductionLoadProbe/1.1", "Cache-Control": "no-cache"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(64 * 1024)
            status = int(response.status)
        error = None
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        error = None
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        status = 0
        error = type(exc).__name__
    return status, (time.perf_counter() - started) * 1000.0, error


def _percentile(rows: list[float], fraction: float) -> float:
    if not rows:
        return 0.0
    index = min(len(rows) - 1, max(0, math.ceil(fraction * len(rows)) - 1))
    return rows[index]


def run_load_probe(config: ProbeConfig) -> dict:
    config = config.validated()
    results: list[tuple[int, float, str | None]] = []
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.concurrency) as pool:
        futures = [pool.submit(_one, config.url, config.timeout_seconds) for _ in range(config.requests)]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    elapsed = max(0.000001, time.perf_counter() - started)
    latencies = sorted(row[1] for row in results)
    success = sum(1 for status, _, error in results if error is None and 200 <= status < 400)
    errors = len(results) - success
    transport_errors = sum(1 for _, _, error in results if error is not None)
    success_ratio = success / len(results) if results else 0.0
    p95 = _percentile(latencies, 0.95)
    p99 = _percentile(latencies, 0.99)

    status_counts: dict[str, int] = {}
    transport_error_types: dict[str, int] = {}
    for status, _, error in results:
        key = str(status) if status else "transport_error"
        status_counts[key] = status_counts.get(key, 0) + 1
        if error:
            transport_error_types[error] = transport_error_types.get(error, 0) + 1

    success_threshold_passed = success_ratio >= float(config.minimum_success_ratio)
    latency_threshold_passed = config.maximum_p95_ms is None or p95 <= float(config.maximum_p95_ms)
    return {
        "ok": bool(success_threshold_passed and latency_threshold_passed),
        "target": config.url,
        "request_count": len(results),
        "concurrency": config.concurrency,
        "success_count": success,
        "error_count": errors,
        "transport_error_count": transport_errors,
        "success_ratio": round(success_ratio, 6),
        "requests_per_second": round(len(results) / elapsed, 3),
        "elapsed_seconds": round(elapsed, 3),
        "latency_ms": {
            "min": round(min(latencies), 3),
            "median": round(statistics.median(latencies), 3),
            "p95": round(p95, 3),
            "p99": round(p99, 3),
            "max": round(max(latencies), 3),
        },
        "status_counts": dict(sorted(status_counts.items())),
        "transport_error_types": dict(sorted(transport_error_types.items())),
        "thresholds": {
            "minimum_success_ratio": float(config.minimum_success_ratio),
            "maximum_p95_ms": float(config.maximum_p95_ms) if config.maximum_p95_ms is not None else None,
            "success_threshold_passed": success_threshold_passed,
            "latency_threshold_passed": latency_threshold_passed,
        },
        "bounded": True,
        "remote_target_explicitly_authorized": bool(config.allow_remote),
        "max_request_cap": _MAX_REQUESTS,
        "max_concurrency_cap": _MAX_CONCURRENCY,
        "evidence_scope": "bounded_http_smoke_not_production_soak_or_capacity_proof",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded Aura production HTTP load smoke probe")
    parser.add_argument("url")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--minimum-success-ratio", type=float, default=1.0)
    parser.add_argument("--maximum-p95-ms", type=float)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = run_load_probe(
        ProbeConfig(
            url=args.url,
            requests=args.requests,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout,
            allow_remote=args.allow_remote,
            minimum_success_ratio=args.minimum_success_ratio,
            maximum_p95_ms=args.maximum_p95_ms,
        )
    )
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(payload, end="")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ProbeConfig", "run_load_probe"]
