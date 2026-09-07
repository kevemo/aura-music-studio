from __future__ import annotations

"""Single additive mount point for the durable Chat 9 product workflow slice."""

from fastapi import APIRouter

# Importing the hardening modules installs service-boundary guards before any workflow
# endpoint is served. They do not add parallel HTTP ingestion or publishing surfaces.
from . import esp_product_workflows_hardening as _product_workflow_hardening  # noqa: F401
from . import esp_product_workflows_lead_hardening as _lead_hardening  # noqa: F401
from . import esp_product_workflows_scheduling_hardening as _scheduling_hardening  # noqa: F401
from .esp_product_workflows import router as product_workflows_router
from .esp_product_workflows_portal import router as product_workflows_portal_router
from .esp_support_conversations import router as support_conversations_router
from .esp_training_academy import router as training_academy_router
from .owner_chat9_dashboard_ui import install_owner_chat9_dashboard_ui
from .owner_chat9_operations import router as owner_chat9_operations_router

# Install the read-only Owner dashboard panel during canonical production composition. This adds
# middleware only; it does not register a competing /owner/dashboard route.
install_owner_chat9_dashboard_ui()

router = APIRouter()
router.include_router(product_workflows_router)
router.include_router(product_workflows_portal_router)
router.include_router(support_conversations_router)
router.include_router(training_academy_router)
router.include_router(owner_chat9_operations_router)

__all__ = ["router"]
