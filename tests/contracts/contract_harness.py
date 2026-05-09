from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .contract_cases import ContractCase, ContractResult


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
        except Exception as exc:  # contract harness reports raw failure detail
            results.append(ContractResult(adapter_name, case.name, "failed", str(exc)))
        else:
            results.append(ContractResult(adapter_name, case.name, "passed"))
    return results
