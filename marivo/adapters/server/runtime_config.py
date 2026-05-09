from __future__ import annotations

from marivo.config import MarivoConfig


class TomlRuntimeConfigAdapter:
    """Wraps ``MarivoConfig``, delegates ``get(key)`` to ``getattr(config, key, None)``.

    The returned value is always converted to ``str`` (or ``None`` if absent)
    to satisfy the ``RuntimeConfig`` protocol.
    """

    def __init__(self, config: MarivoConfig) -> None:
        self._config = config

    def get(self, key: str) -> str | None:
        value = getattr(self._config, key, None)
        if value is None:
            return None
        return str(value)
