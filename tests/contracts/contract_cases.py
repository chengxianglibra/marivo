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


def run_contract_cases(
    *,
    adapter_name: str,
    factory: Callable[[Path], Any],
    cases: list[ContractCase],
    tmp_path: Path,
) -> list[ContractResult]:
    results: list[ContractResult] = []
    adapter = factory(tmp_path)
    for case in cases:
        try:
            case.run(adapter, tmp_path)
        except Exception as exc:
            results.append(ContractResult(adapter_name, case.name, "failed", str(exc)))
        else:
            results.append(ContractResult(adapter_name, case.name, "passed"))
    return results
