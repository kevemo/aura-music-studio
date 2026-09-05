from __future__ import annotations

import base64
import json
import math
import os
import struct
from pathlib import Path
from typing import Any


class GameModelError(ValueError):
    pass


_MAX_VERTICES = max(3, int(os.getenv("AURA_GAME_MODEL_MAX_VERTICES", "100000")))
_MAX_PRIMITIVES = max(1, int(os.getenv("AURA_GAME_MODEL_MAX_PRIMITIVES", "128")))
_MAX_ACCESSOR_ELEMENTS = max(3, int(os.getenv("AURA_GAME_MODEL_MAX_ACCESSOR_ELEMENTS", "250000")))
_MAX_NODES = max(1, int(os.getenv("AURA_GAME_MODEL_MAX_NODES", "4096")))
_MAX_MESHES = max(1, int(os.getenv("AURA_GAME_MODEL_MAX_MESHES", "2048")))
_MAX_ACCESSORS = max(1, int(os.getenv("AURA_GAME_MODEL_MAX_ACCESSORS", "4096")))
_MAX_BUFFER_VIEWS = max(1, int(os.getenv("AURA_GAME_MODEL_MAX_BUFFER_VIEWS", "4096")))
_MAX_BUFFERS = max(1, int(os.getenv("AURA_GAME_MODEL_MAX_BUFFERS", "64")))
_COMPONENT_TYPES = {
    5120: ("b", 1),
    5121: ("B", 1),
    5122: ("h", 2),
    5123: ("H", 2),
    5125: ("I", 4),
    5126: ("f", 4),
}
_TYPE_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _decode_data_uri(uri: str) -> bytes:
    value = str(uri or "")
    if not value.startswith("data:") or ";base64," not in value:
        raise GameModelError("glTF external buffer URLs are not allowed; embed buffers as base64 or use GLB")
    _header, encoded = value.split(",", 1)
    try:
        return base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise GameModelError("glTF contains an invalid embedded base64 buffer") from exc


def _validate_document_shape(document: dict[str, Any]) -> None:
    limits = {
        "nodes": _MAX_NODES,
        "meshes": _MAX_MESHES,
        "accessors": _MAX_ACCESSORS,
        "bufferViews": _MAX_BUFFER_VIEWS,
        "buffers": _MAX_BUFFERS,
    }
    for key, limit in limits.items():
        rows = document.get(key, []) or []
        if not isinstance(rows, list):
            raise GameModelError(f"glTF {key} must be an array")
        if len(rows) > limit:
            raise GameModelError(f"glTF exceeds the {limit} {key} safety limit")
    for row in document.get("accessors", []) or []:
        if not isinstance(row, dict):
            raise GameModelError("glTF accessor entries must be objects")
        try:
            count = int(row.get("count") or 0)
        except (TypeError, ValueError) as exc:
            raise GameModelError("glTF accessor count is invalid") from exc
        if count < 0:
            raise GameModelError("glTF accessor count is invalid")
        if count > _MAX_ACCESSOR_ELEMENTS:
            raise GameModelError(
                f"glTF accessor exceeds the {_MAX_ACCESSOR_ELEMENTS} element safety limit"
            )


def _read_glb(path: Path) -> tuple[dict[str, Any], list[bytes]]:
    payload = path.read_bytes()
    if len(payload) < 20:
        raise GameModelError("GLB file is too small")
    magic, version, declared_length = struct.unpack_from("<4sII", payload, 0)
    if magic != b"glTF" or version != 2 or declared_length != len(payload):
        raise GameModelError("Only structurally valid glTF 2.0 GLB files are supported")
    offset = 12
    json_chunk: bytes | None = None
    binary_chunks: list[bytes] = []
    while offset + 8 <= len(payload):
        chunk_length, chunk_type = struct.unpack_from("<II", payload, offset)
        offset += 8
        end = offset + chunk_length
        if end > len(payload):
            raise GameModelError("GLB chunk exceeds the declared file length")
        chunk = payload[offset:end]
        offset = end
        if chunk_type == 0x4E4F534A and json_chunk is None:
            json_chunk = chunk
        elif chunk_type == 0x004E4942:
            binary_chunks.append(chunk)
    if offset != len(payload) or json_chunk is None:
        raise GameModelError("GLB is missing a valid JSON chunk")
    try:
        document = json.loads(json_chunk.rstrip(b"\x00 \t\r\n").decode("utf-8"))
    except Exception as exc:
        raise GameModelError("GLB JSON chunk is invalid") from exc
    if not isinstance(document, dict):
        raise GameModelError("GLB JSON root must be an object")
    if str(document.get("asset", {}).get("version", "")) != "2.0":
        raise GameModelError("Only glTF 2.0 models are supported")
    _validate_document_shape(document)

    buffers: list[bytes] = []
    binary_index = 0
    for row in document.get("buffers", []) or []:
        if not isinstance(row, dict):
            raise GameModelError("glTF buffer entries must be objects")
        uri = row.get("uri")
        if uri:
            data = _decode_data_uri(str(uri))
        else:
            if binary_index >= len(binary_chunks):
                raise GameModelError("GLB references a missing binary buffer")
            data = binary_chunks[binary_index]
            binary_index += 1
        required = int(row.get("byteLength") or 0)
        if required < 0 or len(data) < required:
            raise GameModelError("GLB buffer is shorter than its declared byteLength")
        buffers.append(data)
    return document, buffers


def _read_gltf(path: Path) -> tuple[dict[str, Any], list[bytes]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GameModelError("glTF JSON is invalid") from exc
    if not isinstance(document, dict):
        raise GameModelError("glTF JSON root must be an object")
    if str(document.get("asset", {}).get("version", "")) != "2.0":
        raise GameModelError("Only glTF 2.0 models are supported")
    _validate_document_shape(document)
    buffers: list[bytes] = []
    for row in document.get("buffers", []) or []:
        if not isinstance(row, dict):
            raise GameModelError("glTF buffer entries must be objects")
        uri = str(row.get("uri") or "")
        if not uri:
            raise GameModelError("JSON glTF buffers must be embedded as base64 data URIs")
        data = _decode_data_uri(uri)
        required = int(row.get("byteLength") or 0)
        if required < 0 or len(data) < required:
            raise GameModelError("glTF buffer is shorter than its declared byteLength")
        buffers.append(data)
    return document, buffers


def _load_document(path: Path) -> tuple[dict[str, Any], list[bytes], str]:
    suffix = path.suffix.lower()
    if suffix == ".glb":
        document, buffers = _read_glb(path)
        return document, buffers, "glb"
    if suffix == ".gltf":
        document, buffers = _read_gltf(path)
        return document, buffers, "gltf"
    raise GameModelError("Game models must use .glb or .gltf")


def _normalized(value: float, component_type: int) -> float:
    if component_type == 5120:
        return max(-1.0, value / 127.0)
    if component_type == 5121:
        return value / 255.0
    if component_type == 5122:
        return max(-1.0, value / 32767.0)
    if component_type == 5123:
        return value / 65535.0
    if component_type == 5125:
        return value / 4294967295.0
    return float(value)


def _finite_values(values: tuple[float, ...]) -> tuple[float, ...]:
    if any(not math.isfinite(value) for value in values):
        raise GameModelError("glTF geometry contains non-finite numeric values")
    return values


def _accessor(document: dict[str, Any], buffers: list[bytes], index: int) -> list[tuple[float, ...]]:
    accessors = document.get("accessors", []) or []
    views = document.get("bufferViews", []) or []
    if not isinstance(index, int) or index < 0 or index >= len(accessors):
        raise GameModelError("glTF primitive references an invalid accessor")
    row = accessors[index]
    if not isinstance(row, dict):
        raise GameModelError("glTF accessor entries must be objects")
    if row.get("sparse"):
        raise GameModelError("Sparse glTF accessors are not supported by the closed runtime yet")
    view_index = row.get("bufferView")
    if not isinstance(view_index, int) or view_index < 0 or view_index >= len(views):
        raise GameModelError("glTF accessor is missing a valid bufferView")
    view = views[view_index]
    if not isinstance(view, dict):
        raise GameModelError("glTF bufferView entries must be objects")
    buffer_index = view.get("buffer")
    if not isinstance(buffer_index, int) or buffer_index < 0 or buffer_index >= len(buffers):
        raise GameModelError("glTF bufferView references a missing buffer")
    component_type = int(row.get("componentType") or 0)
    if component_type not in _COMPONENT_TYPES:
        raise GameModelError("glTF accessor uses an unsupported component type")
    value_type = str(row.get("type") or "")
    component_count = _TYPE_COMPONENTS.get(value_type)
    if component_count is None:
        raise GameModelError("glTF accessor uses an unsupported value type")
    count = int(row.get("count") or 0)
    if count < 0:
        raise GameModelError("glTF accessor count is invalid")
    if count > _MAX_ACCESSOR_ELEMENTS:
        raise GameModelError(f"glTF accessor exceeds the {_MAX_ACCESSOR_ELEMENTS} element safety limit")
    fmt_char, component_size = _COMPONENT_TYPES[component_type]
    element_size = component_size * component_count
    stride = int(view.get("byteStride") or element_size)
    if stride < element_size:
        raise GameModelError("glTF bufferView byteStride is smaller than its accessor element")
    start = int(view.get("byteOffset") or 0) + int(row.get("byteOffset") or 0)
    data = buffers[buffer_index]
    normalized = bool(row.get("normalized", False))
    fmt = "<" + fmt_char * component_count
    values: list[tuple[float, ...]] = []
    for item_index in range(count):
        offset = start + item_index * stride
        if offset < 0 or offset + element_size > len(data):
            raise GameModelError("glTF accessor reads beyond its embedded buffer")
        raw = struct.unpack_from(fmt, data, offset)
        if normalized and component_type != 5126:
            converted = tuple(_normalized(float(value), component_type) for value in raw)
        else:
            converted = tuple(float(value) for value in raw)
        values.append(_finite_values(converted))
    return values


def _identity() -> list[list[float]]:
    return [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def _matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    result = [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]
    for row in result:
        _finite_values(tuple(float(value) for value in row))
    return result


def _node_matrix(node: dict[str, Any]) -> list[list[float]]:
    raw = node.get("matrix")
    if isinstance(raw, list) and len(raw) == 16:
        values = _finite_values(tuple(float(value) for value in raw))
        # glTF stores matrices column-major; convert to ordinary row-major math here.
        return [[values[c * 4 + r] for c in range(4)] for r in range(4)]
    translation = node.get("translation") if isinstance(node.get("translation"), list) else [0.0, 0.0, 0.0]
    scale = node.get("scale") if isinstance(node.get("scale"), list) else [1.0, 1.0, 1.0]
    rotation = node.get("rotation") if isinstance(node.get("rotation"), list) else [0.0, 0.0, 0.0, 1.0]
    if len(translation) != 3 or len(scale) != 3 or len(rotation) != 4:
        raise GameModelError("glTF node has malformed TRS data")
    x, y, z, w = _finite_values(tuple(float(v) for v in rotation))
    sx, sy, sz = _finite_values(tuple(float(v) for v in scale))
    tx, ty, tz = _finite_values(tuple(float(v) for v in translation))
    length = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    if not math.isfinite(length):
        raise GameModelError("glTF node rotation is non-finite")
    x, y, z, w = x / length, y / length, z / length, w / length
    rotation_matrix = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0.0],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0.0],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    scale_matrix = [[sx, 0.0, 0.0, 0.0], [0.0, sy, 0.0, 0.0], [0.0, 0.0, sz, 0.0], [0.0, 0.0, 0.0, 1.0]]
    result = _matmul(rotation_matrix, scale_matrix)
    result[0][3], result[1][3], result[2][3] = tx, ty, tz
    return result


def _transform_point(matrix: list[list[float]], value: tuple[float, ...]) -> tuple[float, float, float]:
    x, y, z = value[:3]
    return _finite_values(
        (
            matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3],
            matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3],
            matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3],
        )
    )


def _transform_normal(matrix: list[list[float]], value: tuple[float, ...]) -> tuple[float, float, float]:
    x, y, z = value[:3]
    nx = matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z
    ny = matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z
    nz = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z
    nx, ny, nz = _finite_values((nx, ny, nz))
    length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    if not math.isfinite(length):
        raise GameModelError("glTF transformed normal is non-finite")
    return nx / length, ny / length, nz / length


def _face_normal(a: tuple[float, float, float], b: tuple[float, float, float], c: tuple[float, float, float]) -> tuple[float, float, float]:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = _finite_values((uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx))
    length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    if not math.isfinite(length):
        raise GameModelError("glTF face normal is non-finite")
    return nx / length, ny / length, nz / length


def extract_static_mesh(path: Path) -> dict[str, Any]:
    """Project glTF 2 geometry into closed triangle data consumed by reviewed Aura3D code.

    No script, external URI, material extension, animation program or executable content from the
    source model is ever emitted into the browser runtime. Embedded model materials are intentionally
    ignored; creators apply Aura Material DNA and verified texture bindings separately.
    """
    path = Path(path)
    document, buffers, source_format = _load_document(path)
    meshes = document.get("meshes", []) or []
    nodes = document.get("nodes", []) or []
    if not meshes:
        raise GameModelError("glTF model does not contain any meshes")

    scene_index = document.get("scene", 0)
    scenes = document.get("scenes", []) or []
    if not isinstance(scenes, list):
        raise GameModelError("glTF scenes must be an array")
    if scenes and isinstance(scene_index, int) and 0 <= scene_index < len(scenes):
        scene = scenes[scene_index]
        if not isinstance(scene, dict):
            raise GameModelError("glTF scene entries must be objects")
        roots = list(scene.get("nodes", []) or [])
    elif nodes:
        child_ids = {
            int(child)
            for node in nodes
            if isinstance(node, dict)
            for child in (node.get("children", []) or [])
            if isinstance(child, int)
        }
        roots = [index for index in range(len(nodes)) if index not in child_ids]
    else:
        roots = []

    instances: list[tuple[int, list[list[float]]]] = []
    visited_path: set[tuple[int, int]] = set()

    def walk(node_index: int, parent: list[list[float]], depth: int) -> None:
        if depth > 64 or not isinstance(node_index, int) or node_index < 0 or node_index >= len(nodes):
            raise GameModelError("glTF node graph is invalid or too deep")
        node = nodes[node_index]
        if not isinstance(node, dict):
            raise GameModelError("glTF node entries must be objects")
        world_matrix = _matmul(parent, _node_matrix(node))
        mesh_index = node.get("mesh")
        if isinstance(mesh_index, int):
            if mesh_index < 0 or mesh_index >= len(meshes):
                raise GameModelError("glTF node references an invalid mesh")
            instances.append((mesh_index, world_matrix))
        children = node.get("children", []) or []
        if not isinstance(children, list):
            raise GameModelError("glTF node children must be an array")
        for child in children:
            marker = (node_index, int(child)) if isinstance(child, int) else (-1, -1)
            if marker in visited_path:
                raise GameModelError("glTF node graph contains a cycle")
            visited_path.add(marker)
            walk(int(child), world_matrix, depth + 1)
            visited_path.discard(marker)

    if roots:
        for root in roots:
            walk(int(root), _identity(), 0)
    else:
        instances = [(index, _identity()) for index in range(len(meshes))]

    vertices: list[float] = []
    primitive_count = 0
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]

    for mesh_index, matrix in instances:
        mesh = meshes[mesh_index]
        if not isinstance(mesh, dict):
            raise GameModelError("glTF mesh entries must be objects")
        primitives = mesh.get("primitives", []) or []
        if not isinstance(primitives, list):
            raise GameModelError("glTF mesh primitives must be an array")
        for primitive in primitives:
            if not isinstance(primitive, dict):
                raise GameModelError("glTF primitive entries must be objects")
            primitive_count += 1
            if primitive_count > _MAX_PRIMITIVES:
                raise GameModelError(f"Model exceeds the {_MAX_PRIMITIVES} primitive safety limit")
            if int(primitive.get("mode", 4)) != 4:
                raise GameModelError("Aura3D static model import currently supports TRIANGLES primitives only")
            attributes = primitive.get("attributes") or {}
            if not isinstance(attributes, dict):
                raise GameModelError("glTF primitive attributes must be an object")
            position_index = attributes.get("POSITION")
            if not isinstance(position_index, int):
                raise GameModelError("glTF mesh primitive is missing POSITION data")
            positions = _accessor(document, buffers, position_index)
            if not positions or any(len(value) != 3 for value in positions):
                raise GameModelError("glTF POSITION accessor must contain VEC3 values")
            normal_index = attributes.get("NORMAL")
            normals = _accessor(document, buffers, normal_index) if isinstance(normal_index, int) else []
            if normals and (len(normals) != len(positions) or any(len(value) != 3 for value in normals)):
                raise GameModelError("glTF NORMAL accessor must align with POSITION")
            uv_index = attributes.get("TEXCOORD_0")
            uvs = _accessor(document, buffers, uv_index) if isinstance(uv_index, int) else []
            if uvs and (len(uvs) != len(positions) or any(len(value) != 2 for value in uvs)):
                raise GameModelError("glTF TEXCOORD_0 accessor must align with POSITION")
            indices_index = primitive.get("indices")
            if isinstance(indices_index, int):
                index_values = _accessor(document, buffers, indices_index)
                if any(len(value) != 1 for value in index_values):
                    raise GameModelError("glTF index accessor must contain SCALAR values")
                indices = [int(value[0]) for value in index_values]
            else:
                indices = list(range(len(positions)))
            if len(indices) % 3:
                raise GameModelError("TRIANGLES primitive index count must be divisible by three")

            for tri_start in range(0, len(indices), 3):
                tri = indices[tri_start : tri_start + 3]
                if any(index < 0 or index >= len(positions) for index in tri):
                    raise GameModelError("glTF primitive contains an out-of-range vertex index")
                transformed = [_transform_point(matrix, positions[index]) for index in tri]
                face = _face_normal(transformed[0], transformed[1], transformed[2])
                for local, source_index in enumerate(tri):
                    if len(vertices) // 8 >= _MAX_VERTICES:
                        raise GameModelError(f"Model exceeds the {_MAX_VERTICES} expanded-vertex safety limit")
                    point = transformed[local]
                    normal = _transform_normal(matrix, normals[source_index]) if normals else face
                    uv = uvs[source_index] if uvs else (0.0, 0.0)
                    uv = _finite_values((float(uv[0]), float(uv[1])))
                    vertices.extend((point[0], point[1], point[2], normal[0], normal[1], normal[2], uv[0], uv[1]))
                    for axis in range(3):
                        minimum[axis] = min(minimum[axis], point[axis])
                        maximum[axis] = max(maximum[axis], point[axis])

    vertex_count = len(vertices) // 8
    if vertex_count == 0:
        raise GameModelError("glTF model has no drawable triangle geometry")
    return {
        "schema_version": 1,
        "source_format": source_format,
        "vertex_stride_floats": 8,
        "vertex_count": vertex_count,
        "triangle_count": vertex_count // 3,
        "primitive_count": primitive_count,
        "vertices": vertices,
        "bounds": {"min": minimum, "max": maximum},
        "animations_present": bool(document.get("animations")),
        "skins_present": bool(document.get("skins")),
        "static_projection_only": True,
        "animation_clips_executed": False,
        "skinning_executed": False,
        "embedded_materials_ignored": True,
        "external_resources_allowed": False,
        "generated_code_executed": False,
    }


__all__ = ["GameModelError", "extract_static_mesh"]
