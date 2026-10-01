"""One monotonic execution deadline shared by all R7 stages."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Timer

from marivo.analysis.core.domain_captures import DomainPreparationError


@dataclass(frozen=True, slots=True)
class ExecuteDeadline:
    start: float
    clock: Callable[[], float] = field(default_factory=lambda: time.monotonic)
    seconds: float = 600.0

    def check(self) -> None:
        if self.clock() - self.start > self.seconds:
            raise DomainPreparationError(
                "r7.execute_timeout",
                "execute",
                "completion within 600 monotonic seconds",
                "execute deadline exceeded",
                "Reduce the bounded preparation or use a qualified source implementation.",
            )

    @contextmanager
    def interrupt_on_expiry(self, interrupt: Callable[[], object]) -> Iterator[None]:
        self.check()
        timer = Timer(
            max(0.0, self.seconds - (self.clock() - self.start)),
            lambda: interrupt() if self.clock() - self.start > self.seconds else None,
        )
        timer.daemon = True
        timer.start()
        try:
            yield
            self.check()
        finally:
            timer.cancel()
            timer.join()


CURRENT: ContextVar[ExecuteDeadline | None] = ContextVar("r7_execute_deadline", default=None)


COMMITTED: ContextVar[bool] = ContextVar("r7_durable_commit", default=False)


def check() -> None:
    if COMMITTED.get():
        return
    deadline = CURRENT.get()
    if deadline is not None:
        deadline.check()


@contextmanager
def execution_budget(*, start: float | None = None) -> Iterator[None]:
    if CURRENT.get() is not None:
        inherited_token = COMMITTED.set(False)
        try:
            yield
            check()
        finally:
            COMMITTED.reset(inherited_token)
        return
    deadline = ExecuteDeadline(time.monotonic() if start is None else start)
    token = CURRENT.set(deadline)
    committed_token = COMMITTED.set(False)
    try:
        yield
        check()
    finally:
        COMMITTED.reset(committed_token)
        CURRENT.reset(token)


@contextmanager
def guard(interrupt: Callable[[], object]) -> Iterator[None]:
    deadline = CURRENT.get()
    if deadline is None:
        yield
    else:
        with deadline.interrupt_on_expiry(interrupt):
            yield
