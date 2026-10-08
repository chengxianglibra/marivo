"""Relay native SIGINT wakeups while an owned blocking read holds the caller."""

from __future__ import annotations

import os
import signal
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Event, Thread, current_thread, main_thread


def _forward(descriptor: int, data: bytes) -> None:
    if descriptor < 0:
        return
    if os.name == "nt":
        borrowed = socket.socket(fileno=descriptor)
        try:
            borrowed.send(data)
        finally:
            borrowed.detach()
    else:
        os.write(descriptor, data)


@contextmanager
def owned_sigint(interrupt: Callable[[], object]) -> Iterator[None]:
    """Wake an owned cancellation callback without replacing Python signal handlers."""
    if (
        current_thread() is not main_thread()
        or signal.getsignal(signal.SIGINT) is not signal.default_int_handler
    ):
        yield
        return
    receiver, sender = socket.socketpair()
    sender.setblocking(False)
    receiver.settimeout(0.05)
    stopped = Event()
    failure: list[BaseException] = []
    previous: int | None = None

    def receive() -> None:
        assert previous is not None
        try:
            while True:
                try:
                    data = receiver.recv(64)
                except TimeoutError:
                    if stopped.is_set():
                        return
                    continue
                if not data:
                    return
                _forward(previous, data)
                if int(signal.SIGINT) in data:
                    interrupt()
        except BaseException as error:
            failure.append(error)

    listener = Thread(target=receive, name="marivo-owned-sigint", daemon=True)
    try:
        previous = signal.set_wakeup_fd(sender.fileno(), warn_on_full_buffer=False)
        try:
            listener.start()
            yield
        finally:
            signal.set_wakeup_fd(previous)
            stopped.set()
            sender.close()
            if listener.ident is not None:
                listener.join()
            if failure:
                raise failure[0]
    finally:
        receiver.close()
        sender.close()
