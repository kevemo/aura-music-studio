from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from .aura_avatar_runtime import RUNTIME_JS, THREE_VERSION, THREE_VRM_VERSION

router = APIRouter(tags=["Aura Embodied Bootstrap"])

# Correct the guarded thinking-state scalar in the source runtime before serving it.
PATCHED_RUNTIME_JS = RUNTIME_JS.replace(
    "this.state==='thinking'?.55:0",
    "(this.state==='thinking'?0.55:0)",
)


def avatar_bootstrap_html() -> str:
    return f"""
<script type='importmap' id='aura-avatar-importmap'>
{{"imports":{{
  "three":"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.module.js",
  "three/addons/":"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/examples/jsm/",
  "@pixiv/three-vrm":"https://cdn.jsdelivr.net/npm/@pixiv/three-vrm@{THREE_VRM_VERSION}/lib/three-vrm.module.js"
}}}}
</script>
<script type='module' src='/aura/avatar/runtime-v3.js' id='aura-avatar-runtime'></script>
"""


@router.get("/aura/avatar/runtime-v3.js", include_in_schema=False)
def runtime_js() -> Response:
    return Response(PATCHED_RUNTIME_JS, media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "private, max-age=300"})
