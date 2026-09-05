from aura_music_studio.native_commerce_api import (
    _public_entitlement_label,
    native_products_pricing,
)
from aura_music_studio.native_products import (
    AURA_OS_ENTITLEMENT,
    AURA_SEC_ENTITLEMENT,
    SLS_PUBLIC_NAME,
)


def test_native_commerce_projects_legacy_entitlement_keys_to_locked_public_labels():
    assert _public_entitlement_label(AURA_OS_ENTITLEMENT) == "Aura OS"
    assert _public_entitlement_label(AURA_SEC_ENTITLEMENT) == SLS_PUBLIC_NAME
    assert "Aura Sec" not in _public_entitlement_label(AURA_SEC_ENTITLEMENT)


def test_public_native_pricing_declares_sls_separate_from_command_center_membership():
    catalogue = native_products_pricing()
    products = {item["id"]: item for item in catalogue["products"]}

    assert catalogue["sls_native_licensing_separate_from_command_center_membership"] is True
    assert products["aura_sec"]["name"] == SLS_PUBLIC_NAME
    assert products["aura_os_sec_bundle"]["name"] == f"Aura OS + {SLS_PUBLIC_NAME}"
    # Compatibility IDs remain stable even though the public product name changed.
    assert products["aura_sec"]["id"] == "aura_sec"
