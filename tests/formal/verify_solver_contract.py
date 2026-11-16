"""Keep solver failures from turning an unproved claim into a green gate."""

import subprocess

import pytest

from tests.formal import _smt


def test_solver_reports_a_real_integer_counterexample() -> None:
    with pytest.raises(AssertionError, match="counterexample:") as error:
        _smt.prove_unsat(
            "(set-logic QF_LIA)\n(declare-const n Int)\n(assert (= n 7))",
            claim="Deliberately false claim",
            witness=("n",),
        )
    assert "(n 7)" in str(error.value)


@pytest.mark.parametrize(
    "code,stdout,stderr",
    (
        (0, "unknown\n", ""),
        (0, "", ""),
        (0, 'unsat\n(error "unsupported syntax")\n', ""),
        (1, "unsat\n", "solver crashed"),
    ),
)
def test_solver_only_accepts_a_successful_unambiguous_unsat(
    monkeypatch: pytest.MonkeyPatch, code: int, stdout: str, stderr: str
) -> None:
    def reply(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess("z3", code, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", reply)
    with pytest.raises(AssertionError):
        _smt.prove_unsat("(assert false)", claim="Unconfirmed claim", witness=())


def test_solver_timeout_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == 10
        raise subprocess.TimeoutExpired("z3", 10)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        _smt.prove_unsat("(assert false)", claim="Timed out claim", witness=())
