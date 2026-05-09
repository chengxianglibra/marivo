from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contract_cases import ContractCase


@dataclass(frozen=True)
class ParityResult:
    case_name: str
    local_status: str
    remote_status: str
    detail: str = ""


def _run_case(adapter: Any, case: ContractCase, tmp_path: Path) -> tuple[str, str]:
    try:
        case.run(adapter, tmp_path)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return "failed", str(exc)
    return "passed", ""


def compare_contract_matrix(
    *,
    local_name: str,
    local_factory: Callable[[Path], Any],
    remote_name: str,
    remote_factory: Callable[[Path], Any],
    cases: list[ContractCase],
    tmp_path: Path,
) -> list[ParityResult]:
    results: list[ParityResult] = []
    local_adapter = local_factory(tmp_path)
    remote_adapter = remote_factory(tmp_path)
    for case in cases:
        local_status, local_detail = _run_case(local_adapter, case, tmp_path)
        remote_status, remote_detail = _run_case(remote_adapter, case, tmp_path)
        detail = ""
        if local_status != remote_status:
            detail = (
                f"status mismatch for {case.name}: "
                f"{local_name}={local_status} {remote_name}={remote_status}"
            )
        elif local_detail != remote_detail:
            detail = (
                f"detail mismatch for {case.name}: "
                f"{local_name}={local_detail!r} {remote_name}={remote_detail!r}"
            )
        results.append(
            ParityResult(
                case_name=case.name,
                local_status=local_status,
                remote_status=remote_status,
                detail=detail,
            )
        )
    return results
