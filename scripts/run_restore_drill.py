from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from aura_music_studio.operational_evidence import run_restore_drill, write_restore_evidence


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the deterministic isolated Command Center backup/restore mechanism drill. "
            "This CLI does not certify restoration of a real production backup."
        )
    )
    parser.add_argument(
        "--environment",
        default="ci",
        choices=["test", "ci", "integration", "staging", "recovery"],
    )
    parser.add_argument("--output", type=Path, required=True, help="Secret-free JSON evidence file to write")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="esp-restore-drill-") as tmp:
        evidence = run_restore_drill(Path(tmp), environment=args.environment)
    output = write_restore_evidence(args.output, evidence)
    print(json.dumps({"evidence": str(output), **evidence}, sort_keys=True))
    return 0 if evidence.get("result") == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
