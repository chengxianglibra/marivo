from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ContractStatus = Literal["passed", "failed", "xfail", "skipped"]


@dataclass(frozen=True)
class ContractCase:
    name: str
    run: Callable[[Any, Path], Any]


@dataclass(frozen=True)
class ContractResult:
    adapter_name: str
    case_name: str
    status: ContractStatus
    detail: str = ""


__all__ = [
    "ContractCase",
    "ContractResult",
    "ContractStatus",
]
