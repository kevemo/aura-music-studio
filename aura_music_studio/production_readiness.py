from __future__ import annotations

from collections.abc import Mapping

from . import _production_readiness_impl as _impl


_original_build_readiness_report = _impl.build_readiness_report


def build_readiness_report(
    environ: Mapping[str, str] | None = None,
    *,
    perform_runtime_probes: bool = False,
) -> dict:
    """Return truthful configuration, serving and release readiness separately.

    ``ok`` intentionally remains the backward-compatible configuration-validation result.
    ``serving_ready`` is stricter: it can only become true when bounded runtime probes were
    actually performed and verified. A config-only report therefore never implies that the
    deployed process can safely serve traffic.
    """

    report = _original_build_readiness_report(
        environ,
        perform_runtime_probes=perform_runtime_probes,
    )

    configuration_ready = bool(report.get("configuration_ready"))
    runtime_details = report.get("categories", {}).get("runtime_dependencies", {})
    runtime_verified = bool(runtime_details.get("ok"))

    serving_blocking = list(report.get("blocking_categories") or [])
    if not perform_runtime_probes or not runtime_verified:
        serving_blocking.append("runtime_dependencies")
    serving_blocking = list(dict.fromkeys(serving_blocking))
    serving_ready = bool(configuration_ready and perform_runtime_probes and runtime_verified)

    restore_verified = bool(report.get("categories", {}).get("restore_evidence", {}).get("ok"))
    release_blocking = list(serving_blocking)
    if report.get("environment") == "production" and not restore_verified:
        release_blocking.append("restore_evidence")
    release_blocking = list(dict.fromkeys(release_blocking))

    production_ready = bool(
        report.get("environment") == "production"
        and serving_ready
        and restore_verified
    )

    report.update(
        {
            "ok": configuration_ready,
            "configuration_ready": configuration_ready,
            "serving_ready": serving_ready,
            "serving_blocking_categories": serving_blocking,
            "release_blocking_categories": release_blocking,
            "production_ready": production_ready,
        }
    )
    return report


# Route handlers defined in the reviewed implementation resolve this module global at call time.
# Rebinding it here applies the stricter truth contract to /health/ready and /internal/metrics
# without duplicating or rewriting their already-reviewed HTTP/authentication behavior.
_impl.build_readiness_report = build_readiness_report

router = _impl.router


def main() -> int:
    return _impl.main()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_readiness_report", "router"]
