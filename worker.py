from __future__ import annotations

import os
import socket

from aura_music_studio.jobs import AuraJobWorker


if __name__ == "__main__":
    worker_id = os.getenv("AURA_WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"
    poll = float(os.getenv("AURA_JOB_POLL_SECONDS", "2"))
    AuraJobWorker(worker_id=worker_id).serve_forever(poll_seconds=poll)
