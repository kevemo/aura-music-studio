from aura_music_studio import aura_avatar_bootstrap
from aura_music_studio import aura_avatar_theme_tools  # noqa: F401
from aura_music_studio import aura_avatar_mobile_loader_tools  # noqa: F401


def test_runtime_loads_mobile_ktx2_and_meshopt_assets():
    runtime = aura_avatar_bootstrap.PATCHED_RUNTIME_JS
    assert runtime.count("/* AURA_MOBILE_GLTF_DECODERS */") == 1
    assert "KTX2Loader" in runtime
    assert "MeshoptDecoder" in runtime
    assert "setKTX2Loader" in runtime
    assert "setMeshoptDecoder" in runtime
    assert "setTranscoderPath" in runtime
    assert "basis/" in runtime
    assert "ktx2.setWorkerLimit(innerWidth<700?1:2)" in runtime


def test_mobile_decoder_patch_preserves_vrm_loader():
    runtime = aura_avatar_bootstrap.PATCHED_RUNTIME_JS
    assert "VRMLoaderPlugin" in runtime
    assert "loader.register(parser=>new VRMLoaderPlugin(parser))" in runtime
    assert "gltf.userData.vrm" in runtime
