"""Run the opt-in solver and surface concrete counterexamples."""

import os
import subprocess
import sys
from pathlib import Path


def _z3(query: str) -> str:
    binary = Path(sys.executable).with_name("z3.exe" if os.name == "nt" else "z3")
    assert binary.is_file(), (
        'Install the formal extra: .venv/bin/python -m pip install -e ".[formal]"'
    )
    result = subprocess.run(
        [str(binary), "-in", "-smt2"],
        input=query,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, f"Z3 failed: {result.stdout}\n{result.stderr}"
    return result.stdout.strip()


def prove_unsat(query: str, *, claim: str, witness: tuple[str, ...]) -> None:
    """Discharge the negated claim, or show the inputs that falsified it."""
    check = query + "\n(check-sat)\n"
    status = _z3(check)
    if status == "sat":
        model = _z3(check + f"(get-value ({' '.join(witness)}))\n")
        raise AssertionError(f"{claim} counterexample:\n{model}")
    assert status == "unsat", f"Unproved {claim}: {status!r}"
