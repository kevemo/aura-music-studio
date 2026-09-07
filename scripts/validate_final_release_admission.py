from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from aura_music_studio.final_release_admission import evaluate_final_release_admission


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the final release evidence package against one exact integration SHA."
    )
    parser.add_argument(
        "--evidence",
        default=os.getenv("LSS_FINAL_RELEASE_EVIDENCE_PATH", ""),
        help="Path to a JSON release-evidence package. No default evidence is trusted.",
    )
    parser.add_argument(
        "--candidate-sha",
        default=os.getenv("RELEASE_CANDIDATE_SHA") or os.getenv("GITHUB_SHA") or "",
        help="Exact 40-character integration commit SHA to admit.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    evidence_path = Path(args.evidence).expanduser() if args.evidence else None
    if evidence_path is None:
        print("Final release admission FAILED: no evidence path supplied.", file=sys.stderr)
        return 2
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Final release admission FAILED: evidence unreadable/invalid: {type(exc).__name__}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("Final release admission FAILED: evidence root must be a JSON object.", file=sys.stderr)
        return 2

    result = evaluate_final_release_admission(payload, candidate_sha=args.candidate_sha)
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    return 0 if result.admissible else 1


if __name__ == "__main__":
    raise SystemExit(main())
