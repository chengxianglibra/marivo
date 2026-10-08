"""Owned signal wakeups preserve borrowed notification state and caller errors."""

from __future__ import annotations

import os
import signal
import socket
from threading import Event, Thread
from unittest.mock import Mock

import pytest

from marivo.datasource import interrupts


@pytest.mark.parametrize("error", [None, RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("notification", [signal.SIGINT, signal.SIGTERM])
def test_owned_signal_restores_and_forwards_borrowed_wakeup(
    monkeypatch: pytest.MonkeyPatch,
    error: type[BaseException] | None,
    notification: signal.Signals,
) -> None:
    receiver, sender = socket.socketpair()
    sender.setblocking(False)
    receiver.settimeout(2)
    previous = signal.set_wakeup_fd(sender.fileno())
    installed: list[int] = []
    set_wakeup = signal.set_wakeup_fd

    def record(descriptor: int, *, warn_on_full_buffer: bool = True) -> int:
        installed.append(descriptor)
        return set_wakeup(descriptor, warn_on_full_buffer=warn_on_full_buffer)

    monkeypatch.setattr(signal, "set_wakeup_fd", record)
    callback = Mock()

    def execute() -> None:
        with interrupts.owned_sigint(callback):
            os.write(installed[0], bytes([notification]))
            if error is not None:
                raise error("caller interrupted")

    try:
        if error is None:
            execute()
        else:
            with pytest.raises(error, match="caller interrupted"):
                execute()
        assert installed[-1] == sender.fileno()
        assert receiver.recv(64) == bytes([notification])
        assert callback.call_count == (1 if notification == signal.SIGINT else 0)
        with pytest.raises(OSError):
            os.fstat(installed[0])
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    finally:
        set_wakeup(previous)
        receiver.close()
        sender.close()


def test_custom_signal_handler_keeps_existing_wakeup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(signal, "getsignal", lambda _signal: signal.SIG_IGN)
    install = Mock()
    monkeypatch.setattr(signal, "set_wakeup_fd", install)
    with interrupts.owned_sigint(Mock()):
        pass
    install.assert_not_called()


def test_borrowed_wakeup_is_forwarded_before_slow_owned_cancellation() -> None:
    receiver, sender = socket.socketpair()
    sender.setblocking(False)
    receiver.settimeout(1)
    previous = signal.set_wakeup_fd(sender.fileno())
    entered = Event()
    released = Event()

    def cancel() -> None:
        entered.set()
        assert released.wait(2)

    try:
        with interrupts.owned_sigint(cancel):
            active = signal.set_wakeup_fd(-1)
            signal.set_wakeup_fd(active)
            os.write(active, bytes([signal.SIGINT]))
            assert entered.wait(1)
            assert receiver.recv(64) == bytes([signal.SIGINT])
            released.set()
    finally:
        released.set()
        signal.set_wakeup_fd(previous)
        receiver.close()
        sender.close()


def test_worker_thread_keeps_existing_wakeup(monkeypatch: pytest.MonkeyPatch) -> None:
    install = Mock()
    monkeypatch.setattr(signal, "set_wakeup_fd", install)
    finished = Event()

    def execute() -> None:
        with interrupts.owned_sigint(Mock()):
            finished.set()

    worker = Thread(target=execute)
    worker.start()
    worker.join(timeout=2)
    assert finished.is_set() and not worker.is_alive()
    install.assert_not_called()


@pytest.mark.parametrize("fault", ["callback", "start"])
def test_signal_failure_restores_original_wakeup(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    installed: list[int] = []
    set_wakeup = signal.set_wakeup_fd

    def record(descriptor: int, *, warn_on_full_buffer: bool = True) -> int:
        installed.append(descriptor)
        return set_wakeup(descriptor, warn_on_full_buffer=warn_on_full_buffer)

    monkeypatch.setattr(signal, "set_wakeup_fd", record)
    if fault == "start":
        monkeypatch.setattr(Thread, "start", Mock(side_effect=RuntimeError("start failed")))
    callback = Mock(side_effect=RuntimeError("callback failed"))
    with (
        pytest.raises(RuntimeError, match=fault + " failed"),
        interrupts.owned_sigint(callback),
    ):
        os.write(installed[0], bytes([signal.SIGINT]))
    with pytest.raises(OSError):
        os.fstat(installed[0])
    assert len(installed) == 2
    restored = set_wakeup(-1)
    set_wakeup(restored)
    assert restored == installed[-1]
