"""Action-local observations of actual driver submissions, never execution authority."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

ExecutionDomain = Literal["source", "local"]


@dataclass(slots=True)
class Submission:
    domain: ExecutionDomain
    role: str
    sql: str
    state: Literal["submitted", "succeeded", "failed"] = "submitted"
    error_type: str | None = None

    def fail(self, error: BaseException) -> None:
        self.state = "failed"
        self.error_type = type(error).__name__


class ObservedExecution:
    """One recorder attached before an adapter performs any owned SQL."""

    def __init__(self) -> None:
        self._observer: Callable[[Submission], None] | None = None
        self._execution_domain: ExecutionDomain = "source"
        self._last_submission: Submission | None = None

    def observe(self, observer: Callable[[Submission], None], domain: ExecutionDomain) -> None:
        self._observer = observer
        self._execution_domain = domain

    @contextmanager
    def submission(self, role: str, sql: str) -> Iterator[Submission]:
        receipt = Submission(self._execution_domain, role, sql)
        self._last_submission = receipt
        if self._observer is not None:
            self._observer(receipt)
        try:
            yield receipt
        except BaseException as error:
            receipt.fail(error)
            raise
        else:
            receipt.state = "succeeded"
