from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

_MAX_REQUESTS = 500
_MAX_CONCURRENCY = 20
_MAX_TIMEOUT_SECONDS = 15.0
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class ProbeConfig:
    url: str
    requests: int = 50
    concurrency: int = 5
    timeout_seconds: float = 5.0
    allow_remote: bool = False

    def validated(self) -> "ProbeConfig":
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Load probe target must be an http(s) URL")
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
        return self


def _one(url: str, timeout: float) -> tuple[int, float]:
    started = time.perf_counter()
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "AuraProductionLoadProbe/1.0", "Cache-Control": "no-cache"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(64 * 1024)
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
    return status, (time.perf_counter() - started) * 1000.0


def run_load_probe(config: ProbeConfig) -> dict:
    config = config.validated()
    results: list[tuple[int, float]] = []
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.concurrency) as pool:
        futures = [pool.submit(_one, config.url, config.timeout_seconds) for _ in range(config.requests)]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    elapsed = max(0.000001, time.perf_counter() - started)
    latencies = sorted(row[1] for row in results)
    success = sum(1 for status, _ in results if 200 <= status < 400)
    errors = len(results) - success
    p95_index = min(len(latencies) - 1, max(0, int(round(0.95 * len(latencies) + 0.499999)) - 1))
    return {
        "ok": errors == 0,
        "target": config.url,
        "request_count": len(results),
        "concurrency": config.concurrency,
        "success_count": success,
        "error_count": errors,
        "requests_per_second": round(len(results) / elapsed, 3),
        "latency_ms": {
            "min": round(min(latencies), 3),
            "median": round(statistics.median(latencies), 3),
            "p95": round(latencies[p95_index], 3),
            "max": round(max(latencies), 3),
        },
        "bounded": True,
        "max_request_cap": _MAX_REQUESTS,
        "max_concurrency_cap": _MAX_CONCURRENCY,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded Aura production HTTP load smoke probe")
    parser.add_argument("url")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--allow-remote", action="store_true")
    args = parser.parse_args()
    report = run_load_probe(
        ProbeConfig(
            url=args.url,
            requests=args.requests,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout,
            allow_remote=args.allow_remote,
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ProbeConfig", "run_load_probe"]
