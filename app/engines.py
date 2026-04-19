from __future__ import annotations

from typing import Any

from app.registry import EngineRegistry, build_analytics_engine
from app.storage.analytics import AnalyticsEngine


class EngineService(EngineRegistry):
    """Compatibility facade over the new registry layer."""


def _build_analytics_engine(engine_type: str, connection: dict[str, Any]) -> AnalyticsEngine:
    return build_analytics_engine(engine_type, connection)
