from __future__ import annotations


class CoreEngine:
    """Pure computation facade for the Marivo analysis engine.

    Phase 4b-1: no I/O, no SemanticLayerService dependency.
    All I/O proxy methods have been moved to MarivoRuntime.
    """

    def __init__(self) -> None:
        # No svc, no I/O — pure computation only.
        pass

    # --- Pure domain computation (delegated to core modules) ---

    def normalize_intent_metric_ref(self, metric_ref: str) -> str:
        from app.core.semantic.typed_resolution import normalize_metric_ref

        return normalize_metric_ref(metric_ref)

    def metric_name_from_ref(self, metric_ref: str) -> str:
        from app.core.semantic.typed_resolution import normalize_metric_ref

        return normalize_metric_ref(metric_ref).removeprefix("metric.")
