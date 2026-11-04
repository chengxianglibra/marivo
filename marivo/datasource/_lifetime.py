"""One owner for confirmed release of a live backend."""

from __future__ import annotations

from threading import Lock

from ibis.backends import BaseBackend


class BackendLease:
    def __init__(self, backend: BaseBackend) -> None:
        self.backend = backend
        self._lock = Lock()
        self._disconnected = False

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._disconnected

    def close(self, *, suppress_errors: bool = True) -> bool:
        with self._lock:
            if self._disconnected:
                return True
            disconnect = getattr(self.backend, "disconnect", None)
            if not callable(disconnect):
                return False
            try:
                disconnect()
            except Exception:
                if not suppress_errors:
                    raise
                return False
            self._disconnected = True
            return True
