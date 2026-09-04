from __future__ import annotations

import sys
import time

from .shared_sky_scheduler import SharedSkyScheduler, SharedSkySchedulerError


def main() -> int:
    scheduler = SharedSkyScheduler()
    health = scheduler.health()
    if not health.enabled:
        print("Shared Sky scheduler is disabled; set SHARED_SKY_SCHEDULER_ENABLED=1 after production validation.")
        return 2

    print(f"Shared Sky scheduler worker started; poll interval={health.poll_seconds}s")
    while True:
        try:
            result = scheduler.run_due()
            if result["claimed"]:
                print(
                    "Shared Sky scheduler tick: "
                    f"claimed={result['claimed']} started={result['started']} failed={len(result['failed'])}"
                )
        except SharedSkySchedulerError as exc:
            print(f"Shared Sky scheduler stopped: {exc}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            # Do not print destination URLs, stream keys or credentials here. The worker
            # emits only the exception class for unexpected failures and retries later.
            print(f"Shared Sky scheduler tick failed: {exc.__class__.__name__}", file=sys.stderr)
        time.sleep(health.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
