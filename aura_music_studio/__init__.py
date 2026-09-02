"""Elevate Souls Productions Content Creation Command Center — Powered by Aura AI.

Elevate Your Soul Through Purposeful Media.

The ``aura_music_studio`` package identifier remains for backwards compatibility with
existing installs, project data, automation and deployment configuration. It is not the
public product brand.
"""

__version__ = "0.20.0"

# Install the provider-cost hard-budget boundary at package import so every ComfyUI renderer
# entrypoint receives the same pre-submission protection. The default remains warning-only;
# hard blocking activates only through deployment-owned environment configuration.
from .provider_budget_enforcement import install_provider_budget_enforcement

install_provider_budget_enforcement()
