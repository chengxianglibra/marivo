"""Private Slice-2 candidate errors for Session runtime reads."""

from __future__ import annotations

from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.introspection.live.model import LiveHelpTarget


def _repair(*, action: str, target: str, snippet: str | None = None) -> AnalysisRepair:
    return AnalysisRepair(
        kind="inspect",
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id=target),
        snippet=snippet,
    )


class ArtifactNotFoundError(AnalysisError):
    @classmethod
    def for_ref(cls, ref: str) -> ArtifactNotFoundError:
        return cls(
            message=f"Artifact {ref!r} does not exist in the current Session",
            expected="an exact Artifact ref owned by the current Session",
            received=ref,
            location="session.artifact(ref)",
            repair=_repair(
                action="Inspect bounded Run history or the Session graph, then retry one exact ref.",
                target="runtime.runs",
                snippet="page = session.runs(limit=20)\nartifact = session.artifact('<ref>')",
            ),
        )


class RunNotFoundError(AnalysisError):
    @classmethod
    def for_id(cls, run_id: str) -> RunNotFoundError:
        return cls(
            message=f"Run {run_id!r} does not exist in the current Session",
            expected="an exact Run id owned by the current Session",
            received=run_id,
            location="session.get_run(run_id)",
            repair=_repair(
                action="Read a bounded Run page and retry one returned Run id.",
                target="runtime.runs",
                snippet="page = session.runs(limit=20)\nrun = session.get_run(page.items[0].run_id)",
            ),
        )


class SessionGraphLimitError(AnalysisError):
    @classmethod
    def for_value(cls, value: object) -> SessionGraphLimitError:
        return cls(
            message="Session graph max_nodes is outside the supported bound",
            expected="max_nodes within [1, 500]",
            received=repr(value),
            location="session.graph(max_nodes=...)",
            repair=_repair(
                action="Pass max_nodes within [1, 500].",
                target="session.graph",
                snippet="graph = session.graph(max_nodes=100)",
            ),
        )


class SessionGraphArgumentError(AnalysisError):
    @classmethod
    def invalid(cls, *, artifact_ref: str | None, direction: object) -> SessionGraphArgumentError:
        return cls(
            message="Session graph focus arguments do not describe a supported traversal",
            expected=(
                "direction='ancestors' for an overall graph, or an exact artifact_ref with "
                "direction='ancestors'|'descendants'"
            ),
            received=f"artifact_ref={artifact_ref!r}, direction={direction!r}",
            location="session.graph(...) arguments",
            repair=_repair(
                action="Pass one exact Artifact ref when requesting descendant traversal.",
                target="session.graph",
                snippet=(
                    "graph = session.graph(artifact_ref='<ref>', "
                    "direction='descendants', max_nodes=100)"
                ),
            ),
        )


class SessionGraphTooLargeError(AnalysisError):
    @classmethod
    def for_count(cls, *, count: int, limit: int) -> SessionGraphTooLargeError:
        return cls(
            message="Session runtime history exceeds the bounded overall graph scan",
            expected=f"at most {limit} combined Run and Artifact records",
            received=str(count),
            location="session.graph() overall scan",
            repair=_repair(
                action=(
                    "Page Runs to obtain an exact Artifact ref, then request one focused graph "
                    "direction."
                ),
                target="runtime.runs",
                snippet=(
                    "page = session.runs(limit=20)\n"
                    "graph = session.graph(artifact_ref='<ref>', direction='ancestors')"
                ),
            ),
        )


class SessionGraphIntegrityError(AnalysisError):
    @classmethod
    def mismatch(
        cls,
        *,
        message: str,
        expected: str,
        received: str,
        location: str,
    ) -> SessionGraphIntegrityError:
        return cls(
            message=message,
            expected=expected,
            received=received,
            location=location,
            repair=_repair(
                action=(
                    "Inspect the named Run and Artifact records, then regenerate the computation "
                    "in a fresh Session when canonical storage is corrupt."
                ),
                target="session.graph",
            ),
        )


__all__: list[str] = []
