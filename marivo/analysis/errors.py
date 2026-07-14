"""Call mv.help() for bounded agent help over the Marivo analysis runtime."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict

from marivo.analysis._capabilities.model import (
    AnalysisToSemanticHandoff,
    EnvironmentFingerprint,
    LiveHelpTarget,
)
from marivo.datasource import errors as _datasource_errors
from marivo.semantic.catalog import SemanticKind

DatasourceFieldInvalidError = _datasource_errors.DatasourceFieldInvalidError
DatasourceSecretInPlaintextError = _datasource_errors.DatasourceSecretInPlaintextError

RepairKind = Literal["retry", "inspect", "semantic_handoff", "environment"]


class AnalysisRepair(BaseModel):
    """Typed repair instruction for an :class:`AnalysisError`.

    Parameters
    ----------
    kind:
        Closed repair category. ``retry`` means the agent can re-attempt with
        a corrected call. ``inspect`` means the agent should gather more
        evidence before proceeding. ``semantic_handoff`` means a required
        semantic object is absent and the marivo-semantic skill must author
        it. ``environment`` means project or datasource state must be
        repaired before retry.
    action:
        One-sentence concrete next step.
    help_target:
        Canonical ``mv.help(...)`` target the agent should consult.
    snippet:
        Optional paste-ready code snippet.
    candidates:
        Optional tuple of live candidate strings (e.g. available metric ids).
    semantic_handoff:
        Typed handoff to the semantic layer, populated when ``kind`` is
        ``semantic_handoff``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: RepairKind
    action: str
    help_target: LiveHelpTarget
    snippet: str | None = None
    candidates: tuple[str, ...] = ()
    semantic_handoff: AnalysisToSemanticHandoff | None = None


class _DerivedFields(TypedDict, total=False):
    """Internal typed dict for derived stable fields.

    Keys are ``expected``, ``received``, ``location``, and ``repair``.
    """

    expected: str
    received: str
    location: str
    repair: AnalysisRepair


class AnalysisError(Exception):
    """Call mv.help(AnalysisError) for its public consumption contract.

    Base class for all analysis errors.
    """

    def __init__(
        self,
        *,
        message: str,
        expected: str | None = None,
        received: str | None = None,
        location: str | None = None,
        repair: AnalysisRepair | None = None,
        hint: str | None = None,
        context: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self._context: dict[str, object] = dict(context) if context else {}

        # Derive stable fields from context if not explicitly provided.
        derived = self._derive_fields()
        self.expected: str | None = expected if expected is not None else derived.get("expected")
        self.received: str | None = received if received is not None else derived.get("received")
        self.location: str | None = location if location is not None else derived.get("location")
        self.repair: AnalysisRepair | None = repair if repair is not None else derived.get("repair")

        if hint is None:
            from marivo.analysis.constraints import CONSTRAINTS
            from marivo.introspection.errors import hint_from_catalog

            hint = hint_from_catalog(
                {constraint.id: constraint for constraint in CONSTRAINTS.values()},
                self.kind,
            )
        self.hint = hint

    @property
    def kind(self) -> str:
        name = type(self).__name__
        return name[:-5] if name.endswith("Error") else name

    def _derive_fields(self) -> _DerivedFields:
        """Override in subtypes to derive stable fields from ``_context``."""

        return _DerivedFields()

    def __str__(self) -> str:
        lines = [f"{type(self).__name__}: {self.message}"]

        context_lines: list[str] = []
        if self.location:
            context_lines.append(f"Location: {self.location}")
        if self.expected:
            context_lines.append(f"Expected: {self.expected}")
        if self.received:
            context_lines.append(f"Received: {self.received}")
        if self.hint:
            context_lines.append(f"Hint: {self.hint}")
        if context_lines:
            lines.append("")
            lines.extend(context_lines)

        if self.repair is not None:
            lines.append("")
            lines.append("Repair:")
            lines.append(f"  {self.repair.action}")
            if self.repair.snippet:
                lines.extend(f"  {line}" for line in self.repair.snippet.splitlines())
            if self.repair.candidates:
                lines.append(f"  Candidates: {', '.join(self.repair.candidates)}")
            lines.append(f"Help: mv.help('{self.repair.help_target.display}')")

        return "\n".join(lines)


def _candidates_preview(available: object, limit: int = 10) -> tuple[str, ...]:
    """Extract a bounded tuple of candidate strings from context."""

    if isinstance(available, (list, tuple)) and available:
        return tuple(str(item) for item in available[:limit])
    return ()


def _cause_with_available(prefix: str, available: object) -> str:
    """Build a cause string with an optional available-ids preview."""

    cause = prefix
    if isinstance(available, (list, tuple)) and available:
        preview = ", ".join(str(item) for item in available[:10])
        suffix = f" Available: {preview}"
        if len(available) > 10:
            suffix += f" (+{len(available) - 10} more)"
        cause += suffix
    return cause


class GrainUnsupportedError(AnalysisError):
    """A requested analysis grain is incompatible with the time field base granularity."""


class MetricNotFoundError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        metric_id = self._context.get("metric_id")
        model = self._context.get("model")
        metric = self._context.get("metric")
        available = self._context.get("available_ids")
        metric_ref: str | None = None
        if isinstance(metric_id, str) and metric_id:
            metric_ref = metric_id
        elif isinstance(model, str) and model and isinstance(metric, str) and metric:
            metric_ref = f"{model}.{metric}"
        if not metric_ref:
            return _DerivedFields()
        candidates = _candidates_preview(available)
        if candidates:
            # Typo case: close matches exist — suggest retry with a candidate.
            cause = f"metric_id={metric_ref} is not registered in the active semantic model."
            cause = _cause_with_available(cause, available)
            return _DerivedFields(
                expected="registered metric semantic object",
                received=metric_ref,
                location="session.observe call",
                repair=AnalysisRepair(
                    kind="retry",
                    action="Use a registered metric id from the catalog.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
                    snippet=(
                        "import marivo.semantic as ms\n"
                        "catalog = ms.load()\n"
                        "catalog.metrics.show()  # confirm the exact id\n"
                        'session.observe(catalog.get("metric.<registered_metric_id>"), '
                        'time_scope={"start": "2026-07-01", "end": "2026-10-01"})'
                    ),
                    candidates=candidates,
                ),
            )
        # Absent case: no close matches — the metric must be authored/registered
        # in the semantic layer before analysis can proceed.
        return _DerivedFields(
            expected="registered metric semantic object",
            received=metric_ref,
            location="session.observe call",
            repair=AnalysisRepair(
                kind="semantic_handoff",
                action=(
                    f"metric_id={metric_ref} has no close match in the loaded "
                    "catalog; author and register the metric in the semantic "
                    "layer, then reload and retry."
                ),
                help_target=LiveHelpTarget(surface="semantic"),
                snippet=(
                    "import marivo.semantic as ms\n"
                    "ms.help('authoring')  # read the authoring workflow\n"
                    "ms.help('metric')     # choose the correct metric constructor\n"
                    "# After authoring, reload and re-observe:\n"
                    "catalog = ms.load()\n"
                    'session.observe(catalog.get("metric.<new_metric_id>"), '
                    'time_scope={"start": "2026-07-01", "end": "2026-10-01"})'
                ),
                semantic_handoff=AnalysisToSemanticHandoff(
                    required_kind=SemanticKind.METRIC,
                    requirement=f"metric_id={metric_ref} is not registered in the active semantic model",
                    affected_capability_id="observe",
                    environment_fingerprint=EnvironmentFingerprint.current(),
                ),
            ),
        )


class WindowInvalidError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        window = self._context.get("window") or self._context.get("time_scope")
        window_ref = window if isinstance(window, str) and window else "<time_scope>"
        fix_snippet = self._context.get("fix_snippet")
        candidates = _candidates_preview(self._context.get("candidates"))
        return _DerivedFields(
            received=window_ref,
            location="session.observe time_scope or frame window argument",
            repair=AnalysisRepair(
                kind="retry",
                action="Pass a parseable absolute time_scope.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
                snippet=(
                    str(fix_snippet)
                    if isinstance(fix_snippet, str) and fix_snippet
                    else (
                        'session.observe(session.catalog.get("metric.sales.revenue"), '
                        'time_scope={"start": "2026-07-01", "end": "2026-10-01"})'
                    )
                ),
                candidates=candidates,
            ),
        )


class TimezoneInvalidError(AnalysisError):
    pass


class DataTypeMismatchError(AnalysisError):
    pass


class WindowAmbiguousError(AnalysisError): ...


class SliceInvalidError(AnalysisError): ...


class SliceAmbiguousError(AnalysisError): ...


class SemanticKindMismatchError(AnalysisError):
    @staticmethod
    def _catalog_expected_label(argument: str, expected_kind: str) -> str:
        """Return the human-readable label for what a catalog argument requires."""

        if argument == "time_dimension":
            return "time dimension"
        if expected_kind == "dimension":
            return "dimension or time dimension"
        return expected_kind

    def _derive_fields(self) -> _DerivedFields:
        if str(self._context.get("missing")) == "search_space":
            return _DerivedFields(
                location="session.discover.driver_axes arguments",
                repair=AnalysisRepair(
                    kind="retry",
                    action="Pass a non-empty search_space with catalog dimension refs.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="discover"),
                    snippet=(
                        'region = session.catalog.get("dimension.sales.orders.region").ref\n'
                        "session.discover.driver_axes(delta, search_space=[region])"
                    ),
                ),
            )
        got_semantic_shape = self._context.get("got_semantic_shape")
        expected_semantic_shape = self._context.get("expected_semantic_shape")
        if isinstance(got_semantic_shape, str) and isinstance(expected_semantic_shape, str):
            frame_kind = self._context.get("frame_kind")
            frame_ref = frame_kind if isinstance(frame_kind, str) and frame_kind else "frame"
            return _DerivedFields(
                expected=expected_semantic_shape,
                received=got_semantic_shape,
                location=f"{frame_ref}.as_{expected_semantic_shape}() narrowing",
                repair=AnalysisRepair(
                    kind="retry",
                    action=(
                        f"Check semantic_shape before narrowing; "
                        f"as_{expected_semantic_shape}() requires a "
                        f"{expected_semantic_shape} frame."
                    ),
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="artifacts"),
                    snippet=(
                        f'if frame.semantic_shape == "{expected_semantic_shape}":\n'
                        f"    typed = frame.as_{expected_semantic_shape}()"
                    ),
                ),
            )
        intent = self._context.get("intent")
        predicted_semantic_shape = self._context.get("predicted_semantic_shape")
        expect_shape = self._context.get("expect_shape")
        if (
            isinstance(intent, str)
            and isinstance(predicted_semantic_shape, str)
            and isinstance(expect_shape, str)
        ):
            return _DerivedFields(
                expected=expect_shape,
                received=predicted_semantic_shape,
                location=f"session.{intent}(expect_shape=...) guard",
                repair=AnalysisRepair(
                    kind="retry",
                    action="Match expect_shape to the predicted semantic shape.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id=intent),
                    snippet=(
                        f'frame = session.{intent}(metric, expect_shape="{predicted_semantic_shape}")'
                    ),
                ),
            )
        got_attribution_shape = self._context.get("got_attribution_shape")
        expected_attribution_shape = self._context.get("expected_attribution_shape")
        if isinstance(got_attribution_shape, str) and isinstance(expected_attribution_shape, str):
            frame_kind = self._context.get("frame_kind")
            frame_ref = frame_kind if isinstance(frame_kind, str) and frame_kind else "frame"
            return _DerivedFields(
                expected=expected_attribution_shape,
                received=got_attribution_shape,
                location=f"{frame_ref}.as_{expected_attribution_shape}() narrowing",
                repair=AnalysisRepair(
                    kind="retry",
                    action=(
                        f"Check attribution_shape before narrowing; "
                        f"as_{expected_attribution_shape}() requires a "
                        f"{expected_attribution_shape} attribution frame."
                    ),
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
                    snippet=(
                        f'if frame.attribution_shape == "{expected_attribution_shape}":\n'
                        f"    typed = frame.as_{expected_attribution_shape}()"
                    ),
                ),
            )
        got_shape = self._context.get("got_shape")
        expected_shape = self._context.get("expected_shape")
        if isinstance(got_shape, str) and isinstance(expected_shape, str):
            return _DerivedFields(
                expected=expected_shape,
                received=got_shape,
                location="CandidateSet.as_<shape>() narrowing",
                repair=AnalysisRepair(
                    kind="retry",
                    action=f"Check CandidateSet.shape before as_{expected_shape}().",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="discover"),
                    snippet=(
                        'if cands.meta.shape == "' + str(expected_shape) + '":\n'
                        "    typed = cands.as_" + str(expected_shape) + "()"
                    ),
                ),
            )
        row_count = self._context.get("row_count")
        requested_rank = self._context.get("requested_rank")
        if isinstance(row_count, int) and isinstance(requested_rank, int):
            return _DerivedFields(
                expected="rank within candidate set row count",
                received=f"rank={requested_rank}, row_count={row_count}",
                location="CandidateSet.select rank argument",
                repair=AnalysisRepair(
                    kind="retry",
                    action="Use a rank within the candidate set's row count.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="discover"),
                    snippet=(
                        "if cands.meta.row_count >= 1:\n"
                        '    value = cands.select(rank=1, attribute="...")'
                    ),
                ),
            )
        shape = self._context.get("shape")
        attribute = self._context.get("attribute")
        valid_fields = self._context.get("valid_fields")
        if isinstance(shape, str) and isinstance(attribute, str):
            valid_list = (
                ", ".join(sorted(valid_fields))
                if isinstance(valid_fields, list) and valid_fields
                else None
            )
            cause = f"select(attribute={attribute!r}) is not available on a CandidateSet[{shape}]."
            if valid_list:
                cause += f" Valid attributes for shape {shape!r}: {valid_list}."
            first_valid = (
                sorted(valid_fields)[0]
                if isinstance(valid_fields, list) and valid_fields
                else "score"
            )
            return _DerivedFields(
                received=f"attribute={attribute!r}, shape={shape}",
                location="CandidateSet.select attribute argument",
                repair=AnalysisRepair(
                    kind="retry",
                    action=cause,
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="discover"),
                    snippet=(f'value = cands.select(rank=1, attribute="{first_valid}")'),
                    candidates=tuple(sorted(valid_fields))
                    if isinstance(valid_fields, list) and valid_fields
                    else (),
                ),
            )
        objective = self._context.get("objective")
        source_kind_value = self._context.get("source_kind")
        semantic_kind_value = self._context.get("semantic_kind")
        expected_kind_raw = self._context.get("expected_kind")
        expected_kind_str = (
            expected_kind_raw
            if isinstance(expected_kind_raw, str) and expected_kind_raw
            else "<allowed>"
        )
        if (
            isinstance(objective, str)
            and isinstance(source_kind_value, str)
            and isinstance(semantic_kind_value, str)
        ):
            return _DerivedFields(
                expected=f"semantic_kind in {expected_kind_str}",
                received=f"semantic_kind={semantic_kind_value!r}, source_kind={source_kind_value!r}",
                location="session.discover dispatch",
                repair=AnalysisRepair(
                    kind="retry",
                    action=(
                        f"discover objective {objective!r} does not accept "
                        f"semantic_kind {semantic_kind_value!r} on a {source_kind_value}."
                    ),
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="discover"),
                ),
            )
        if isinstance(objective, str) and isinstance(source_kind_value, str):
            return _DerivedFields(
                expected=f"source kind in {expected_kind_str}",
                received=f"source_kind={source_kind_value!r}",
                location="session.discover dispatch",
                repair=AnalysisRepair(
                    kind="retry",
                    action=(
                        f"discover objective {objective!r} does not accept source kind "
                        f"{source_kind_value!r}."
                    ),
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="discover"),
                ),
            )
        if expected_kind_raw == "implemented_objective":
            return _DerivedFields(
                location="session.discover dispatch",
                repair=AnalysisRepair(
                    kind="inspect",
                    action=f"discover objective {objective!r} is not yet implemented in this build.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="discover"),
                ),
            )
        # Measure-rejection shape: a measure SemanticRef was passed where a
        # dimension group-by axis is required.
        actual_kind_raw = self._context.get("actual_kind")
        expected_kind_raw_2 = self._context.get("expected_kind")
        argument_raw = self._context.get("argument")
        repair_raw = self._context.get("repair")
        if (
            isinstance(actual_kind_raw, str)
            and actual_kind_raw == "measure"
            and isinstance(expected_kind_raw_2, str)
            and expected_kind_raw_2 == "dimension"
            and not (isinstance(argument_raw, str) and argument_raw)
            and "got_kind" not in self._context
        ):
            ref = self._context.get("ref")
            ref_text = ref if isinstance(ref, str) and ref else "<ref>"
            cause = (
                f"{ref_text!r} is a measure, which is aggregated, not a group-by "
                "axis; slice by a categorical dimension or aggregate it into a metric."
            )
            available = self._context.get("available_ids")
            cause = _cause_with_available(cause, available)
            candidates = _candidates_preview(available)
            fix_snippet = (
                "\n".join(str(line) for line in repair_raw)
                if isinstance(repair_raw, list) and repair_raw
                else None
            )
            return _DerivedFields(
                expected="dimension SemanticRef or CatalogObject",
                received=f"measure ref {ref_text!r}",
                location="session call dimension argument",
                repair=AnalysisRepair(
                    kind="retry",
                    action=cause,
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
                    snippet=fix_snippet,
                    candidates=candidates,
                ),
            )
        # _reject_kind details shape: catalog semantic-kind mismatch at input
        # normalization boundaries.
        argument = self._context.get("argument")
        actual_kind_value = self._context.get("actual_kind")
        expected_kind_for_catalog = self._context.get("expected_kind")
        if (
            isinstance(argument, str)
            and argument
            and isinstance(actual_kind_value, str)
            and actual_kind_value
            and isinstance(expected_kind_for_catalog, str)
            and expected_kind_for_catalog
            and "got_kind" not in self._context
        ):
            label = self._catalog_expected_label(argument, expected_kind_for_catalog)
            cause = (
                f"{argument} requires a {label} SemanticRef or CatalogObject, "
                f"received a {actual_kind_value}."
            )
            available = self._context.get("available_ids")
            cause = _cause_with_available(cause, available)
            candidates = _candidates_preview(available)
            repair = self._context.get("repair")
            fix_snippet = (
                "\n".join(str(line) for line in repair)
                if isinstance(repair, list) and repair
                else None
            )
            return _DerivedFields(
                expected=f"{label} SemanticRef or CatalogObject",
                received=actual_kind_value,
                location=f"session call {argument} argument",
                repair=AnalysisRepair(
                    kind="retry",
                    action=cause,
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
                    snippet=fix_snippet,
                    candidates=candidates,
                ),
            )
        got_kind = self._context.get("got_kind")
        expected_kind = self._context.get("expected_kind")
        if not (
            isinstance(got_kind, str)
            and got_kind
            and isinstance(expected_kind, str)
            and expected_kind
        ):
            return _DerivedFields()
        if expected_kind == "candidate_set":
            return _DerivedFields(
                expected=expected_kind,
                received=got_kind,
                location="CandidateSet.select call",
                repair=AnalysisRepair(
                    kind="retry",
                    action="CandidateSet.select only operates on CandidateSet artifacts.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="discover"),
                    snippet=(
                        "cands = session.discover.point_anomalies(metric)\n"
                        'window = cands.select(rank=1, attribute="window")'
                    ),
                ),
            )
        if expected_kind == "metric":
            return _DerivedFields(
                expected=expected_kind,
                received=got_kind,
                location="session.observe call",
                repair=AnalysisRepair(
                    kind="retry",
                    action="observe requires a catalog metric object or ref.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
                    snippet=(
                        'session.observe(session.catalog.get("metric.sales.revenue"), '
                        'time_scope={"start": "2026-07-01", "end": "2026-10-01"})'
                    ),
                ),
            )
        if got_kind != "delta_frame" or expected_kind != "metric_frame":
            return _DerivedFields(
                expected=expected_kind,
                received=got_kind,
                repair=AnalysisRepair(
                    kind="retry",
                    action="Input frame kind does not match the requested analysis operation.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
                ),
            )
        return _DerivedFields(
            expected=expected_kind,
            received=got_kind,
            location="session.compare call",
            repair=AnalysisRepair(
                kind="retry",
                action="Pass an observe result (MetricFrame) instead of a compare result (DeltaFrame).",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
                snippet=(
                    'revenue = session.catalog.get("metric.sales.revenue")\n'
                    'cur  = session.observe(revenue, time_scope={"start": "2026-07-01", "end": "2026-10-01"})\n'
                    'base = session.observe(revenue, time_scope={"start": "2025-07-01", "end": "2025-10-01"})\n'
                    "delta = session.compare(cur, base, alignment=mv.window_bucket())"
                ),
            ),
        )


class AlignmentFailedError(AnalysisError): ...


class DiscoverInsufficientDataError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        objective = self._context.get("objective")
        row_count = self._context.get("row_count")
        minimum = self._context.get("minimum")
        objective_ref = objective if isinstance(objective, str) and objective else "period_shifts"
        count_ref = row_count if isinstance(row_count, int) else "<row_count>"
        minimum_ref = minimum if isinstance(minimum, int) else 4
        return _DerivedFields(
            received=f"{count_ref} usable bucket(s)",
            expected=f"at least {minimum_ref} time buckets",
            location="session.discover.period_shifts input",
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    f"discover objective {objective_ref!r} needs at least {minimum_ref} "
                    f"time buckets in one series."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="discover"),
                snippet=(
                    "delta = session.compare(cur, base, alignment=mv.window_bucket())\n"
                    'session.discover.period_shifts(delta, value="delta")  # use a wider window'
                ),
            ),
        )


class AlignmentPolicyValidationError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        case = self._context.get("case")
        kind = self._context.get("kind")
        if case == "missing_calendar":
            kind_str = kind if isinstance(kind, str) and kind else "dow_aligned"
            return _DerivedFields(
                location="mv.AlignmentPolicy(...)",
                repair=AnalysisRepair(
                    kind="retry",
                    action=f"alignment kind {kind_str!r} requires a calendar.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="alignment"),
                    snippet=(
                        f"mv.{kind_str}(\n"
                        '    calendar=mv.CalendarRef("cn_holidays"),\n'
                        '    period="month",\n'
                        ")"
                    ),
                ),
            )
        if case == "legacy_calendar_bucket":
            return _DerivedFields(
                location="mv.AlignmentPolicy(...)",
                repair=AnalysisRepair(
                    kind="retry",
                    action="Use 'window_bucket' for request-window bucket spine alignment.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="alignment"),
                    snippet="mv.window_bucket()",
                ),
            )
        if case == "unexpected_calendar":
            return _DerivedFields(
                location="mv.AlignmentPolicy(...)",
                repair=AnalysisRepair(
                    kind="retry",
                    action="window_bucket alignment does not accept a calendar argument.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="alignment"),
                    snippet="mv.window_bucket()  # no calendar argument",
                ),
            )
        return _DerivedFields(
            location="mv.AlignmentPolicy(...)",
        )


class PromotionFailedError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        target_kind = self._context.get("target_kind")
        missing = self._context.get("missing")
        target = target_kind if isinstance(target_kind, str) and target_kind else "frame"
        if isinstance(missing, list) and missing:
            missing_fields = {str(field) for field in missing}
            snippet = (
                (
                    "session.promote_metric_frame(\n"
                    "    scratch,\n"
                    '    metric=session.catalog.get("metric.sales.revenue"),\n'
                    '    semantic_kind="segmented",\n'
                    '    measure_column="value",\n'
                    '    axes={"country": session.catalog.get("dimension.sales.orders.country").ref},\n'
                    '    semantic_model="sales",\n'
                    ")"
                )
                if missing_fields & {"metric", "measure_column", "semantic_model"}
                else ("Pass the missing typed ref or column name shown in error details.")
            )
            return _DerivedFields(
                location=f"session.promote_{target}",
                repair=AnalysisRepair(
                    kind="retry",
                    action=f"Promotion is missing required metadata: {', '.join(map(str, missing))}.",
                    help_target=LiveHelpTarget(
                        surface="analysis", canonical_id="boundary.derive_metric_frame"
                    ),
                    snippet=snippet,
                ),
            )
        ambiguous = self._context.get("ambiguous")
        catalog_misses = [
            str(item).removeprefix("metric_not_in_catalog:")
            for item in (ambiguous if isinstance(ambiguous, list) else [])
            if str(item).startswith("metric_not_in_catalog:")
        ]
        if catalog_misses:
            return _DerivedFields(
                received=f"metric '{catalog_misses[0]}' not in catalog",
                location=f"session.promote_{target}",
                repair=AnalysisRepair(
                    kind="retry",
                    action="Use a metric id defined in the loaded semantic catalog.",
                    help_target=LiveHelpTarget(
                        surface="analysis", canonical_id="boundary.derive_metric_frame"
                    ),
                    snippet=(
                        "import marivo.semantic as ms\n"
                        "catalog = ms.load()\n"
                        "catalog.metrics.show()  # pick a defined metric id, then re-promote"
                    ),
                ),
            )
        return _DerivedFields(
            location=f"session.promote_{target}",
            repair=AnalysisRepair(
                kind="retry",
                action="Promotion metadata is incomplete or ambiguous.",
                help_target=LiveHelpTarget(
                    surface="analysis", canonical_id="boundary.derive_metric_frame"
                ),
            ),
        )


class TestShapeNotTestableError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        return _DerivedFields(
            location="session.hypothesis_test call",
            repair=AnalysisRepair(
                kind="retry",
                action="mean_changed needs paired observations; re-observe with enough history.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="hypothesis_test"),
                snippet=(
                    'revenue = session.catalog.get("metric.sales.revenue")\n'
                    'cur = session.observe(revenue, time_scope={"start": "2026-07-01", "end": "2026-08-01"}, grain="day")\n'
                    'base = session.observe(revenue, time_scope={"start": "2025-07-01", "end": "2025-08-01"}, grain="day")\n'
                    "session.hypothesis_test(cur, base)"
                ),
            ),
        )


class TestPolicyError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        return _DerivedFields(
            location="session.hypothesis_test policy arguments",
            repair=AnalysisRepair(
                kind="retry",
                action="hypothesis_test v1 only supports mean_changed, window_bucket alignment, and shape-compatible SamplingPolicy.pairing.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="hypothesis_test"),
                snippet="session.hypothesis_test(cur, base, sampling=mv.SamplingPolicy(pairing='window_bucket'), alpha=0.05)",
            ),
        )


class TestAlignmentError(AlignmentFailedError):
    def _derive_fields(self) -> _DerivedFields:
        return _DerivedFields(
            location="session.hypothesis_test alignment",
            repair=AnalysisRepair(
                kind="retry",
                action="The input frames did not produce any paired samples after alignment and null dropping.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="hypothesis_test"),
                snippet="session.hypothesis_test(cur, base, alignment=mv.window_bucket())",
            ),
        )


class ForecastShapeUnsupportedError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        return _DerivedFields(
            location="session.forecast input frame",
            repair=AnalysisRepair(
                kind="retry",
                action="forecast v1 accepts only MetricFrame time_series or panel shapes.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="forecast"),
                snippet=(
                    'history = session.observe(session.catalog.get("metric.sales.revenue"), time_scope={"start": "2026-01-01", "end": "2026-04-01"}, grain="day")\n'
                    "session.forecast(history, horizon=30)"
                ),
            ),
        )


class ForecastPolicyError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        return _DerivedFields(
            location="session.forecast policy arguments",
            repair=AnalysisRepair(
                kind="retry",
                action="horizon, interval_level, model, seasonality_period, or grain is outside the v1 supported contract.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="forecast"),
                snippet="session.forecast(history, horizon=30, model='seasonal_naive', seasonality_period=7, interval_level=0.95)",
            ),
        )


class ForecastInsufficientHistoryError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        return _DerivedFields(
            location="session.forecast history",
            repair=AnalysisRepair(
                kind="retry",
                action="The time_series input has fewer training points than the selected model requires.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="forecast"),
                snippet=(
                    'history = session.observe(session.catalog.get("metric.sales.revenue"), '
                    'time_scope={"start": "2026-01-01", "end": "2026-04-01"}, grain="day")'
                ),
            ),
        )


class ForecastInputQualityError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        return _DerivedFields(
            location="session.forecast history data",
            repair=AnalysisRepair(
                kind="retry",
                action="Forecast does not silently impute NaN values or fill missing time buckets.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="forecast"),
                snippet="clean = history.transform.window(window={...})  # or impute upstream before forecasting",
            ),
        )


class QualityShapeUnsupportedError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        return _DerivedFields(
            location="session.assess_quality target",
            repair=AnalysisRepair(
                kind="retry",
                action="assess_quality v1 only supports MetricFrame targets.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="assess_quality"),
                snippet="report = session.assess_quality(metric_frame)",
            ),
        )


class MetricShapeUnsupportedError(AnalysisError):
    pass


class FrameMetaInvalidError(AnalysisError):
    pass


class MetricArityError(AnalysisError):
    """An intent that requires a single-metric frame received a multi-metric frame."""


class TransformShapeUnsupportedError(AnalysisError):
    """Raised when an op requires axes the input frame does not have."""


class TransformArgError(AnalysisError):
    """Raised when transform kwargs are missing, wrong type, or contradict the op."""


class TransformDimensionNotFoundError(AnalysisError):
    """Raised when a where / drop_axes target is not present in frame axes."""


class CalendarNotFoundError(AnalysisError):
    pass


class CalendarPolicyError(AnalysisError):
    pass


class CrossBackendMetricError(AnalysisError): ...


class CrossSessionFrameError(AnalysisError): ...


class FrameMutationError(AnalysisError): ...


class FrameReadError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        return _DerivedFields(
            location="frame.show()",
            repair=AnalysisRepair(
                kind="retry",
                action="Use frame.show() for bounded inspection or frame.to_pandas() for terminal custom analysis.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="artifacts"),
                snippet="frame.show()",
            ),
        )


class FrameRefNotFound(AnalysisError): ...  # noqa: N818


class JobNotFoundError(AnalysisError): ...


class FrameCacheCorruptedError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        ref = self._context.get("ref", "?")
        cause = self._context.get("cause", "unknown")
        return _DerivedFields(
            location=f"frame cache for ref '{ref}'",
            repair=AnalysisRepair(
                kind="environment",
                action=f"Persisted frame data is unreadable: {cause}. Delete the corrupted artifact directory to force re-computation.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="recovery"),
                snippet=f"# rm -rf .marivo/analysis/sessions/*/frames/{ref}/",
            ),
        )


class BackendError(AnalysisError): ...


class NoBackendFactoryError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        datasource = self._context.get("datasource")
        if not (isinstance(datasource, str) and datasource):
            return _DerivedFields(
                location="analysis runtime backend configuration",
                repair=AnalysisRepair(
                    kind="environment",
                    action=(
                        "Session has no backend factory configured; data-materializing "
                        "analysis intents need a datasource, backends={...}, or backend_factory=..."
                    ),
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="datasources"),
                    snippet=(
                        "import marivo.analysis as mv\n"
                        "import marivo.datasource as md\n"
                        "\n"
                        "# Recommended: persist the project datasource config once.\n"
                        'md.register(md.DuckDBSpec(name="tiny_orders", path=":memory:"))\n'
                        'session = mv.session.get_or_create(name="analysis")  # auto-loads from datasource\n'
                        "\n"
                        "# Or pass an explicit factory (no datasource lookup):\n"
                        "import ibis\n"
                        "session = mv.session.get_or_create("
                        'name="analysis", '
                        'backend_factory=lambda name: ibis.duckdb.connect(":memory:"), '
                        "use_datasources=False)"
                    ),
                ),
            )
        return _DerivedFields(
            location="analysis runtime datasource backend factory",
            received=f"datasource={datasource!r}",
            repair=AnalysisRepair(
                kind="environment",
                action=(
                    f"datasource={datasource!r} resolved to None "
                    "or a non-ibis object; the analysis runtime needs a live ibis backend."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="datasources"),
                snippet=(
                    "import marivo.analysis as mv\n"
                    "import marivo.datasource as md\n"
                    "\n"
                    'md.register(md.DuckDBSpec(name="tiny_orders", path=":memory:"))\n'
                ),
            ),
        )


DatasourceMissingError = _datasource_errors.DatasourceMissingError
DatasourceSecretStorePermissionsError = _datasource_errors.DatasourceSecretStorePermissionsError
DatasourceEnvVarMissingError = _datasource_errors.DatasourceEnvVarMissingError
DatasourceBackendTypeUnsupportedError = _datasource_errors.DatasourceBackendTypeUnsupportedError
DatasourceSchemaVersionError = _datasource_errors.DatasourceSchemaVersionError
DatasourceConnectionError = _datasource_errors.DatasourceConnectionError
DatasourcePreviewError = _datasource_errors.DatasourcePreviewError
DatasourceMetadataError = _datasource_errors.DatasourceMetadataError


class HelpTargetError(AnalysisError):
    """Call mv.help(HelpTargetError) for its public consumption contract."""

    def __init__(self, *, target: object, suggestions: tuple[str, ...]) -> None:
        received = target if isinstance(target, str) else type(target).__name__
        super().__init__(
            message="analysis help target is not registered",
            expected=(
                "None, canonical target string, registered public callable/type, "
                "public analysis object, semantic object/ref, or AnalysisError"
            ),
            received=str(received),
            location="mv.help.target",
            repair=AnalysisRepair(
                kind="inspect",
                action="Use a canonical registered target from mv.help().",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="help"),
                candidates=suggestions,
            ),
        )


class DuplicateSessionNameError(AnalysisError): ...


class NoActiveSessionError(AnalysisError): ...


class SessionStateError(AnalysisError): ...


class SessionTimezoneConflict(SessionStateError):  # noqa: N818
    def _derive_fields(self) -> _DerivedFields:
        persisted = self._context.get("persisted_report_tz", "<persisted>")
        requested = self._context.get("requested_report_tz", "<requested>")
        return _DerivedFields(
            expected=f"report_timezone={persisted!r}",
            received=f"report_timezone={requested!r}",
            location="mv.session.get_or_create(report_timezone=...)",
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    "Use the persisted report timezone, create a new session, "
                    "or delete and recreate this session to re-bucket under a new report timezone."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="session"),
            ),
        )


class SemanticProjectNotReadyError(AnalysisError): ...


class DimensionFieldNotFoundError(SemanticKindMismatchError):
    def _derive_fields(self) -> _DerivedFields:
        dim = self._context.get("dimension_id")
        datasets = self._context.get("searched_datasets")
        metric_shape = self._context.get("metric_shape")
        available = self._context.get("available_ids")
        dim_ref = dim if isinstance(dim, str) and dim else "<dimension>"
        dataset_list = (
            ", ".join(datasets) if isinstance(datasets, list) and datasets else "<datasets>"
        )
        if metric_shape == "derived":
            cause = (
                f"dimension {dim_ref!r} was not found on the derived metric's "
                f"component datasets or reachable relationship graph ({dataset_list})."
            )
        else:
            cause = (
                f"dimension {dim_ref!r} is not a field on any of the metric's "
                f"datasets ({dataset_list})."
            )
        candidates = _candidates_preview(available)
        if candidates:
            # Close matches exist — suggest retry with a candidate dimension.
            cause = _cause_with_available(cause, available)
            return _DerivedFields(
                expected="dimension or time dimension on the metric's datasets",
                received=dim_ref,
                location="session.observe dimensions argument",
                repair=AnalysisRepair(
                    kind="retry",
                    action=cause,
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
                    snippet=(
                        "import marivo.semantic as ms\n"
                        "catalog = ms.load()\n"
                        "catalog.dimensions.show()  # confirm available dimensions per entity\n"
                        'session.observe(catalog.get("metric.sales.revenue"), '
                        'dimensions=[catalog.get("dimension.<existing_dimension>").ref])'
                    ),
                    candidates=candidates,
                ),
            )
        # No close matches — the dimension must be authored/registered in the
        # semantic layer before analysis can slice by it.
        return _DerivedFields(
            expected="dimension or time dimension on the metric's datasets",
            received=dim_ref,
            location="session.observe dimensions argument",
            repair=AnalysisRepair(
                kind="semantic_handoff",
                action=(
                    f"dimension {dim_ref!r} has no close match on the metric's "
                    "datasets; author and register the dimension in the semantic "
                    "layer, then reload and retry."
                ),
                help_target=LiveHelpTarget(surface="semantic"),
                snippet=(
                    "import marivo.semantic as ms\n"
                    "ms.help('authoring')          # read the authoring workflow\n"
                    "ms.help('dimension_column')   # or ms.help('dimension') for expression bodies\n"
                    "# After authoring, reload and re-observe:\n"
                    "catalog = ms.load()\n"
                    'session.observe(catalog.get("metric.sales.revenue"), '
                    'dimensions=[catalog.get("dimension.<new_dimension>").ref])'
                ),
                semantic_handoff=AnalysisToSemanticHandoff(
                    required_kind=SemanticKind.DIMENSION,
                    requirement=f"dimension {dim_ref} is not found on the metric's datasets",
                    affected_capability_id="observe",
                    environment_fingerprint=EnvironmentFingerprint.current(),
                ),
            ),
        )


class AmbiguousDimensionError(SemanticKindMismatchError):
    def _derive_fields(self) -> _DerivedFields:
        dim = self._context.get("dimension_id")
        candidates = self._context.get("candidates")
        dim_ref = dim if isinstance(dim, str) and dim else "<dimension>"
        candidate_list = (
            ", ".join(candidates) if isinstance(candidates, list) and candidates else "<candidates>"
        )
        return _DerivedFields(
            received=f"dimension {dim_ref!r} matches multiple datasets ({candidate_list})",
            location="session.observe dimensions argument",
            repair=AnalysisRepair(
                kind="retry",
                action="v1 requires unique dimension names across a metric's datasets.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
                candidates=tuple(str(c) for c in candidates)
                if isinstance(candidates, list) and candidates
                else (),
            ),
        )


class DimensionAcrossDatasetsError(SemanticKindMismatchError):
    def _derive_fields(self) -> _DerivedFields:
        mapping = self._context.get("dimensions_by_dataset")
        return _DerivedFields(
            location="session.observe dimensions argument",
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    "All dimensions must resolve to the same dataset in v1; "
                    f"got dimensions_by_dataset={mapping!r}."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
            ),
        )


class AxisNotInPanelDimensionsError(SemanticKindMismatchError):
    def _derive_fields(self) -> _DerivedFields:
        axis = self._context.get("axis")
        available = self._context.get("available_dimensions")
        axis_ref = axis if isinstance(axis, str) and axis else "<axis>"
        available_list = (
            ", ".join(available) if isinstance(available, list) and available else "<dimensions>"
        )
        first_available = (
            available[0] if isinstance(available, list) and available else "<existing_dimension>"
        )
        return _DerivedFields(
            expected=f"axis in panel dimensions ({available_list})",
            received=axis_ref,
            location="session.attribute axes argument",
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    f"axis={axis_ref!r} is not in the panel frame dimensions "
                    f"({available_list}); attribute requires axis to be one of the frame's "
                    "segment dimensions."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
                snippet=(
                    f"# Choose the full catalog ref for panel dimension column {first_available!r}.\n"
                    'axis = session.catalog.get("dimension.<domain.entity.dimension>").ref\n'
                    "session.attribute(delta, axes=[axis])"
                ),
                candidates=tuple(str(a) for a in available)
                if isinstance(available, list) and available
                else (),
            ),
        )


class PanelGrainMismatchError(AlignmentFailedError):
    pass


class SegmentDimensionMismatchError(AlignmentFailedError):
    def _derive_fields(self) -> _DerivedFields:
        current_dims = self._context.get("current_dimensions")
        baseline_dims = self._context.get("baseline_dimensions")
        if not isinstance(current_dims, list) or not isinstance(baseline_dims, list):
            return _DerivedFields()
        cur = ", ".join(current_dims)
        base = ", ".join(baseline_dims)
        extra_current = sorted(set(current_dims) - set(baseline_dims))
        extra_baseline = sorted(set(baseline_dims) - set(current_dims))
        cause = f"segment dimensions differ: current=[{cur}] vs baseline=[{base}]."
        if extra_current:
            cause += f" Extra in current: {', '.join(extra_current)}."
        if extra_baseline:
            cause += f" Extra in baseline: {', '.join(extra_baseline)}."
        return _DerivedFields(
            location="session.compare call",
            repair=AnalysisRepair(
                kind="retry",
                action=cause,
                help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
                snippet=(
                    "metric = session.catalog.get('metric.model.metric')\n"
                    'common_dim = session.catalog.get("dimension.model.entity.common_dim").ref\n'
                    "current = session.observe(metric, dimensions=[common_dim])\n"
                    "baseline = session.observe(metric, dimensions=[common_dim])\n"
                    "delta = session.compare(current, baseline, "
                    "alignment=mv.window_bucket())"
                ),
            ),
        )


class AlignmentPolicyNotApplicableError(AlignmentFailedError):
    pass


class EvidenceStoreUnavailableError(AnalysisError): ...


class FindingExtractionFailedError(AnalysisError): ...


class EvidencePartialError(AnalysisError): ...


class FollowupGenerationRuleViolatedError(AnalysisError): ...


class SchemaVersionMismatchError(AnalysisError): ...


class MigrationFailedError(AnalysisError): ...


class SessionLockedByAnotherProcessError(AnalysisError): ...


class PropositionNotFoundError(AnalysisError): ...


class CumulativeFrameUnsupportedError(AnalysisError):
    """Intent received a cumulative frame whose running-total semantics are unsupported.

    Cumulative metrics store monotonically increasing running totals anchored to
    all history.  Compare, attribute, decompose, and forecast operate on
    period-level flow values; feeding a cumulative frame produces deltas or
    forecasts of running totals rather than of the underlying flow.

    The error teaches the agent to re-observe the base flow metric (the
    ``base`` field in the cumulative marker) and retry the intent on that
    frame instead.
    """

    def __init__(
        self,
        *,
        intent: str,
        frame_ref: str,
        metric_id: str | None,
        cumulative: Mapping[str, object],
    ) -> None:
        base = cumulative.get("base")
        components = cumulative.get("components")
        if base is None and isinstance(components, dict):
            base = ", ".join(
                sorted(
                    str(payload.get("base"))
                    for payload in components.values()
                    if isinstance(payload, dict)
                )
            )
        if intent == "forecast":
            hint = "Forecast the base flow metric instead of the all-history running total."
        else:
            hint = (
                "Use the base flow metric for this intent. A cumulative delta over a "
                "window equals the base total over that window."
            )
        super().__init__(
            message=f"{intent} does not support cumulative metric frames.",
            hint=hint,
            context={
                "intent": intent,
                "frame_ref": frame_ref,
                "metric_id": metric_id,
                "base_metric_id": base,
                "cumulative": dict(cumulative),
            },
        )

    def _derive_fields(self) -> _DerivedFields:
        intent = self._context.get("intent")
        intent_str = intent if isinstance(intent, str) and intent else "<intent>"
        base = self._context.get("base_metric_id")
        base_str = base if isinstance(base, str) and base else None
        return _DerivedFields(
            expected="period-level flow metric frame",
            received="cumulative metric frame",
            location=f"session.{intent_str}",
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    f"Re-observe the base flow metric ({base_str}) "
                    f"and retry {intent_str} on that frame."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id=intent_str),
            ),
        )


class ComponentFrameUnavailableError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        return _DerivedFields(
            location="frame.components()",
            repair=AnalysisRepair(
                kind="inspect",
                action=(
                    "Component frames are only available for derived ratio or "
                    "weighted-average frames produced by component-aware observe/compare."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="artifacts"),
                snippet=(
                    'frame = session.observe(session.catalog.get("metric.model.derived_ratio"))\n'
                    "components = frame.components()"
                ),
            ),
        )


class ComponentFrameMismatchError(AnalysisError):
    pass


class ComponentDecompositionError(AnalysisError):
    pass


class AttributionMaterializationError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        missing_axes = self._context.get("missing_axes")
        if isinstance(missing_axes, list) and missing_axes:
            axis_text = ", ".join(str(axis) for axis in missing_axes)
        else:
            axis_text = "<requested axes>"
        return _DerivedFields(
            location="session.attribute missing-axis materialization",
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    f"Attribute could not materialize missing axes ({axis_text}) from "
                    "the input DeltaFrame lineage without guessing."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
                snippet=(
                    "cur = session.observe(metric, time_scope=current_window, dimensions=[axis])\n"
                    "base = session.observe(metric, time_scope=baseline_window, dimensions=[axis])\n"
                    "delta = session.compare(cur, base)\n"
                    "drivers = session.attribute(delta, axes=[axis])"
                ),
            ),
        )
