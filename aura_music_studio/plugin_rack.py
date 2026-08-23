from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Literal

import soundfile as sf
from pydantic import BaseModel, Field


class PluginDefinition(BaseModel):
    id: str
    name: str
    category: str = "effect"
    format: Literal["vst3", "audio_unit", "external", "lv2"] = "vst3"
    path: str
    enabled: bool = True
    description: str = ""
    license_note: str = "Administrator must verify the plugin licence permits this deployment."
    parameter_hints: dict[str, dict] = Field(default_factory=dict)


class PluginInstance(BaseModel):
    plugin_id: str
    enabled: bool = True
    parameters: dict[str, float | int | bool | str] = Field(default_factory=dict)


class PluginRackRequest(BaseModel):
    instances: list[PluginInstance] = Field(min_length=1, max_length=20)


def _catalog_path() -> Path:
    return Path(os.getenv("AURA_PLUGIN_CATALOG_PATH", "config/plugin_catalog.json")).resolve()


def _allowed_dirs() -> list[Path]:
    configured = os.getenv("AURA_PLUGIN_ALLOWED_DIRS", "/opt/aura/plugins,/usr/lib/vst3,/usr/local/lib/vst3")
    return [Path(x.strip()).resolve() for x in configured.split(",") if x.strip()]


def _path_is_allowed(path: Path) -> bool:
    resolved = path.resolve()
    return any(resolved == root or root in resolved.parents for root in _allowed_dirs())


def load_plugin_catalog() -> list[PluginDefinition]:
    path = _catalog_path()
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("plugins", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("Plugin catalog must contain a list or {'plugins': [...]} object")
    definitions = [PluginDefinition.model_validate(row) for row in rows]
    seen = set()
    for definition in definitions:
        if definition.id in seen:
            raise ValueError(f"Duplicate plugin id: {definition.id}")
        seen.add(definition.id)
        plugin_path = Path(definition.path)
        if not plugin_path.is_absolute():
            raise ValueError(f"Plugin path must be absolute for {definition.id}")
        if not _path_is_allowed(plugin_path):
            raise PermissionError(f"Plugin {definition.id} is outside AURA_PLUGIN_ALLOWED_DIRS")
    return definitions


def public_plugin_catalog() -> list[dict]:
    rows = []
    for definition in load_plugin_catalog():
        item = definition.model_dump(exclude={"path"})
        item["installed"] = Path(definition.path).exists()
        rows.append(item)
    return rows


def _definition_map() -> dict[str, PluginDefinition]:
    return {item.id: item for item in load_plugin_catalog() if item.enabled}


def validate_rack(request: PluginRackRequest) -> list[tuple[PluginDefinition, PluginInstance]]:
    definitions = _definition_map()
    result = []
    for instance in request.instances:
        if not instance.enabled:
            continue
        definition = definitions.get(instance.plugin_id)
        if not definition:
            raise PermissionError(f"Plugin is not owner-approved/enabled: {instance.plugin_id}")
        path = Path(definition.path)
        if not path.exists():
            raise FileNotFoundError(path)
        if not _path_is_allowed(path):
            raise PermissionError(f"Plugin path is outside the approved plugin directories: {instance.plugin_id}")
        result.append((definition, instance))
    if not result:
        raise ValueError("No enabled plugins in rack")
    return result


def _render_external(source: Path, output: Path, rack: list[tuple[PluginDefinition, PluginInstance]]) -> Path | None:
    command = (os.getenv("AURA_PLUGIN_HOST_CMD") or "").strip()
    if not command:
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    rack_payload = [
        {
            "definition": definition.model_dump(),
            "instance": instance.model_dump(),
        }
        for definition, instance in rack
    ]
    env = os.environ.copy()
    env.update({
        "AURA_PLUGIN_INPUT": str(source.resolve()),
        "AURA_PLUGIN_OUTPUT": str(output.resolve()),
        "AURA_PLUGIN_RACK_JSON": json.dumps(rack_payload),
        "AURA_PLUGIN_ALLOWED_DIRS": os.getenv("AURA_PLUGIN_ALLOWED_DIRS", ""),
    })
    subprocess.run(shlex.split(command), env=env, check=True)
    if not output.exists():
        raise RuntimeError("Configured plugin host completed without creating output")
    return output


def _set_plugin_parameter(plugin, name: str, value) -> None:
    # Pedalboard external-plugin parameters are exposed as attributes/properties. Reject unknown
    # controls instead of silently ignoring misspellings or exposing arbitrary object attributes.
    if name.startswith("_") or not hasattr(plugin, name):
        raise ValueError(f"Plugin parameter is not exposed by host: {name}")
    current = getattr(plugin, name)
    try:
        if hasattr(current, "raw_value"):
            current.raw_value = value
        else:
            setattr(plugin, name, value)
    except Exception as exc:
        raise ValueError(f"Could not set plugin parameter {name}: {exc}") from exc


def _render_pedalboard(source: Path, output: Path, rack: list[tuple[PluginDefinition, PluginInstance]]) -> Path:
    try:
        from pedalboard import Pedalboard, load_plugin
    except Exception as exc:
        raise RuntimeError("Pedalboard is not installed and AURA_PLUGIN_HOST_CMD is not configured") from exc

    plugins = []
    for definition, instance in rack:
        if definition.format not in {"vst3", "audio_unit"}:
            raise RuntimeError(
                f"{definition.format} plugin {definition.id} requires AURA_PLUGIN_HOST_CMD; Pedalboard host supports approved VST3/AudioUnit paths here."
            )
        plugin = load_plugin(definition.path)
        for name, value in instance.parameters.items():
            _set_plugin_parameter(plugin, name, value)
        plugins.append(plugin)

    audio, sr = sf.read(source, always_2d=True, dtype="float32")
    board = Pedalboard(plugins)
    # Pedalboard expects channel-first floating-point audio.
    processed = board(audio.T, sr)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, processed.T, sr, subtype="PCM_24")
    return output


def process_plugin_rack(source: Path, output: Path, request: PluginRackRequest) -> tuple[Path, dict]:
    """Render a trusted native-plugin rack over a real-audio asset.

    Plugin binaries are selected only by catalog id. Member input never becomes an executable path
    or shell fragment. Native plugins still execute code, so deployment owners must install/review
    and licence them deliberately and should run the host in an isolated worker/container.
    """
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    rack = validate_rack(request)
    rendered = _render_external(source, output, rack)
    backend = "external_isolated_host"
    if rendered is None:
        rendered = _render_pedalboard(source, output, rack)
        backend = "python_pedalboard"
    return rendered, {
        "backend": backend,
        "plugins": [
            {
                "id": definition.id,
                "name": definition.name,
                "format": definition.format,
                "parameters": instance.parameters,
            }
            for definition, instance in rack
        ],
        "native_code_warning": True,
        "owner_approved_catalog_only": True,
    }
