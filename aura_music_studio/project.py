from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from .models import ProjectManifest

LEGACY_ACE_MODELS = {
    "acestep-v15-xl-turbo": "acestep-v15-turbo",
    "ace-step-v15-xl-turbo": "acestep-v15-turbo",
}


class ProjectWorkspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.input_dir = self.root / "input"
        self.work_dir = self.root / "work"
        self.output_dir = self.root / "output"
        self.logs_dir = self.root / "logs"
        for p in (self.input_dir, self.work_dir, self.output_dir, self.logs_dir):
            p.mkdir(parents=True, exist_ok=True)

    @property
    def manifest_path(self) -> Path:
        for name in ("project.yaml", "project.yml", "project.json"):
            p = self.root / name
            if p.exists():
                return p
        raise FileNotFoundError(f"No project manifest found in {self.root}")

    def load_manifest(self) -> ProjectManifest:
        p = self.manifest_path
        if p.suffix.lower() == ".json":
            data = json.loads(p.read_text(encoding="utf-8"))
        else:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        manifest = ProjectManifest.model_validate(data)
        model = (manifest.renderer.model or "").strip()
        if model in LEGACY_ACE_MODELS:
            manifest.renderer.model = LEGACY_ACE_MODELS[model]
        return manifest

    def resolve_asset(self, value: str | None) -> Path | None:
        if not value:
            return None
        p = Path(value)
        if p.is_absolute():
            return p
        direct = self.root / p
        if direct.exists():
            return direct
        in_input = self.input_dir / p
        return in_input

    def save_json(self, name: str, data: dict) -> Path:
        p = self.work_dir / name
        p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return p

    def copy_to_output(self, source: Path, name: str | None = None) -> Path:
        target = self.output_dir / (name or source.name)
        shutil.copy2(source, target)
        return target

    def log(self, message: str, filename: str = "aura.log") -> None:
        with (self.logs_dir / filename).open("a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
