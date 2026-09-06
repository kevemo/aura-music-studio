from __future__ import annotations

import argparse
from pathlib import Path

from aura_music_studio.legacy_migration_admission import validate_repository


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the legacy Aura→Rhiannon migration provenance register and tracked legacy archives."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    root = args.root.resolve()
    errors = validate_repository(root)
    if errors:
        print("Legacy migration admission FAILED:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(
        "Legacy migration admission passed: provenance register is structurally valid and no prohibited/unverified tracked legacy archive was admitted."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
