"""Phase 13 Shadow Mode.

Public SDK surface (``from evalgate.shadow import shadow``) plus the backend
building blocks (``persistence`` / ``rollup`` / ``alert``) used by the API
router and the ``evalgate shadow`` CLI.
"""

from __future__ import annotations

from evalgate.shadow.sdk import ShadowClient, drain_background_tasks, shadow, spec_hash

__all__ = ["ShadowClient", "drain_background_tasks", "shadow", "spec_hash"]
