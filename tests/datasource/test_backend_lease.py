"""BackendLease reports release only after disconnect is confirmed."""

from __future__ import annotations

from threading import Event, Lock, Thread

import pytest

from marivo.datasource._lifetime import BackendLease


class _TrackingLock:
    """Count threads inside ``close`` so a test can see the waiter block."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._meta = Lock()
        self._inside = 0
        self.second_entered = Event()

    def __enter__(self) -> _TrackingLock:
        with self._meta:
            self._inside += 1
            if self._inside >= 2:
                self.second_entered.set()
        self._lock.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._lock.release()
        with self._meta:
            self._inside -= 1


class _DisconnectBackend:
    def __init__(self, *, fail_until: int = 0) -> None:
        self.calls = 0
        self.fail_until = fail_until
        self.entered = Event()
        self.release = Event()
        self.saw_release = False

    def disconnect(self) -> None:
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            self.saw_release = self.release.wait(timeout=5)
        if self.calls <= self.fail_until:
            raise RuntimeError("connection release failed")


def test_failed_close_retries_disconnect_and_closed_means_confirmed() -> None:
    backend = _DisconnectBackend(fail_until=2)
    backend.release.set()
    lease = BackendLease(backend)

    assert lease.close() is False
    assert lease.closed is False
    with pytest.raises(RuntimeError, match="connection release failed"):
        lease.close(suppress_errors=False)
    assert lease.closed is False
    assert backend.calls == 2

    assert lease.close(suppress_errors=False) is True
    assert lease.closed is True
    assert lease.close() is True
    assert backend.calls == 3


def test_missing_disconnect_stays_unreleased_until_a_later_close() -> None:
    class Backend:
        def __init__(self) -> None:
            self.calls = 0

    backend = Backend()
    lease = BackendLease(backend)

    assert lease.close() is False
    assert lease.close(suppress_errors=False) is False
    assert lease.closed is False

    def disconnect() -> None:
        backend.calls += 1

    backend.disconnect = disconnect
    assert lease.close() is True
    assert lease.closed is True
    assert backend.calls == 1
    assert lease.close() is True
    assert backend.calls == 1


def _overlapping_close(
    monkeypatch: pytest.MonkeyPatch, *, fail_first: bool
) -> tuple[bool, bool, BackendLease, _DisconnectBackend]:
    created: list[_TrackingLock] = []

    class CapturingLock(_TrackingLock):
        def __init__(self) -> None:
            super().__init__()
            created.append(self)

    monkeypatch.setattr("marivo.datasource._lifetime.Lock", CapturingLock)
    backend = _DisconnectBackend(fail_until=1 if fail_first else 0)
    lease = BackendLease(backend)
    tracking = created[0]
    results: dict[str, bool] = {}
    errors: list[BaseException] = []

    def run(name: str) -> None:
        try:
            results[name] = lease.close()
        except BaseException as exc:
            errors.append(exc)

    first = Thread(target=run, args=("first",))
    second = Thread(target=run, args=("second",))
    first.start()
    try:
        assert backend.entered.wait(timeout=5)
        second.start()
        assert tracking.second_entered.wait(timeout=5)
        assert results == {}
        backend.release.set()
        first.join(timeout=5)
        second.join(timeout=5)
    finally:
        backend.release.set()
        first.join(timeout=5)
        second.join(timeout=5)

    assert errors == []
    assert not first.is_alive()
    assert not second.is_alive()
    assert backend.saw_release is True
    return results["first"], results["second"], lease, backend


def test_concurrent_close_shares_one_confirmed_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second, lease, backend = _overlapping_close(monkeypatch, fail_first=False)

    assert first is True
    assert second is True
    assert backend.calls == 1
    assert lease.closed is True


def test_concurrent_close_retries_when_the_in_flight_attempt_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second, lease, backend = _overlapping_close(monkeypatch, fail_first=True)

    assert first is False
    assert second is True
    assert backend.calls == 2
    assert lease.closed is True
