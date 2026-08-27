from __future__ import annotations

import os

from .game_forge_model_assets import _verified_mesh, find_game_model


_MAX_RUNTIME_MODEL_VERTICES = max(
    3,
    int(os.getenv("AURA_GAME_RUNTIME_MODEL_MAX_VERTICES", "250000")),
)


def runtime_bound_model_manifest(game_id: str, model_ids: set[str] | list[str] | tuple[str, ...]) -> list[dict]:
    """Return only model geometry referenced by the current World DNA.

    Uploaded-but-unused models stay in the private model library and never inflate playtest HTML.
    The aggregate expanded-vertex limit is independent of the per-model parser limit so a game
    cannot combine many individually valid meshes into an unbounded browser payload.
    """
    rows: list[dict] = []
    total_vertices = 0
    for model_id in sorted({str(value) for value in model_ids if str(value)}):
        record = find_game_model(game_id, model_id)
        mesh = _verified_mesh(record)
        vertex_count = int(mesh.get("vertex_count") or 0)
        if vertex_count <= 0:
            raise ValueError(f"Game model '{record.label}' has no drawable vertices")
        total_vertices += vertex_count
        if total_vertices > _MAX_RUNTIME_MODEL_VERTICES:
            raise ValueError(
                f"Bound 3D models exceed the {_MAX_RUNTIME_MODEL_VERTICES} expanded-vertex runtime budget"
            )
        rows.append(
            {
                "id": record.id,
                "kind": "model",
                "label": record.label,
                "role": record.role,
                "sha256": record.source_sha256,
                "byte_size": record.byte_size,
                "mesh": mesh,
            }
        )
    return rows


__all__ = ["runtime_bound_model_manifest"]
