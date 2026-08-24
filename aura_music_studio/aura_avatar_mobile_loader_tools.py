from __future__ import annotations

from . import aura_avatar_bootstrap

_MARKER = "/* AURA_MOBILE_GLTF_DECODERS */"

if _MARKER not in aura_avatar_bootstrap.PATCHED_RUNTIME_JS:
    runtime = aura_avatar_bootstrap.PATCHED_RUNTIME_JS
    runtime = runtime.replace(
        "import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';",
        "import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';\n"
        "import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';\n"
        "import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';\n"
        "/* AURA_MOBILE_GLTF_DECODERS */",
        1,
    )

    old = (
        "const loader=new GLTFLoader(); loader.register(parser=>new VRMLoaderPlugin(parser)); "
        "const gltf=await loader.loadAsync(url); const vrm=gltf.userData.vrm;"
    )
    new = (
        "const loader=new GLTFLoader(); loader.register(parser=>new VRMLoaderPlugin(parser)); "
        "const ktx2=new KTX2Loader(); "
        "ktx2.setTranscoderPath('https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/libs/basis/'); "
        "ktx2.setWorkerLimit(innerWidth<700?1:2); ktx2.detectSupport(this.renderer); "
        "loader.setKTX2Loader(ktx2); loader.setMeshoptDecoder(MeshoptDecoder); "
        "let gltf; try{gltf=await loader.loadAsync(url);}finally{ktx2.dispose();} "
        "const vrm=gltf.userData.vrm;"
    )
    if old not in runtime:
        raise RuntimeError("Aura runtime loadModel signature changed; mobile decoder patch was not applied")
    runtime = runtime.replace(old, new, 1)
    aura_avatar_bootstrap.PATCHED_RUNTIME_JS = runtime
