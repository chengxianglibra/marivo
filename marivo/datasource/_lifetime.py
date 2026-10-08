"""One owner for releasing a live backend, including late worker results."""

from __future__ import annotations

from threading import Lock, get_ident

from ibis.backends import BaseBackend


class BackendLease:
    def __init__(self, backend: BaseBackend, *, thread_affine: bool = False) -> None:
        self.backend = backend
        self._owner_thread = get_ident() if thread_affine else None
        self._lock = Lock()
        self._closed = False
        self._disconnected = False

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self, *, suppress_errors: bool = True) -> bool:
        # A timed-out round-trip must release SQLite on its original worker.
        # The worker's finally block retries cleanup when the driver returns.
        if suppress_errors and self._owner_thread is not None and self._owner_thread != get_ident():
            return False
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
