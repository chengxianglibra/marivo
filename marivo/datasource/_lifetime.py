"""One owner for idempotent release of a live backend."""

from __future__ import annotations

from threading import Lock

from ibis.backends import BaseBackend


class BackendLease:
    def __init__(self, backend: BaseBackend) -> None:
        self.backend = backend
        self._lock = Lock()
        self._closed = False
        self._disconnected = False

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self, *, suppress_errors: bool = True) -> bool:
        with self._lock:
            if self._closed:
                return self._disconnected
            self._closed = True
        disconnect = getattr(self.backend, "disconnect", None)
        if callable(disconnect):
            try:
                disconnect()
                self._disconnected = True
            except Exception:
                if not suppress_errors:
                    raise

        return self._disconnected
