from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .pipeline import AuraPipeline


class AuraAutopilot:
    """Simple autonomous project worker.

    A project is any folder containing project.yaml/project.yml/project.json.
    Completed projects are skipped unless force=True. Failed projects can be retried automatically.
    """

    def __init__(self, inbox: str | Path = "projects", poll_seconds: int = 60):
        self.inbox = Path(inbox).resolve()
        self.poll_seconds = poll_seconds
        self.inbox.mkdir(parents=True, exist_ok=True)

    def discover(self) -> list[Path]:
        projects = []
        for child in sorted(self.inbox.iterdir()):
            if not child.is_dir():
                continue
            if any((child / name).exists() for name in ("project.yaml", "project.yml", "project.json")):
                projects.append(child)
        return projects

    def run_once(self, force: bool = False) -> list[dict]:
        results = []
        for project in self.discover():
            status_file = project / "aura_status.json"
            if status_file.exists() and not force:
                try:
                    old = json.loads(status_file.read_text(encoding="utf-8"))
                    if old.get("success") is True:
                        continue
                except Exception:
                    pass
            try:
                result = AuraPipeline(project).run()
            except Exception as exc:
                result = {
                    "project": project.name,
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "time": datetime.now(timezone.utc).isoformat(),
                }
            results.append(result)
        return results

    def serve_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(self.poll_seconds)
