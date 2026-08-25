"""Typed analysis errors with unified Marivo help repairs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict

from marivo.analysis._cumulative import cumulative_compare_blocker
from marivo.datasource import errors as _datasource_errors
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import Ref, SemanticKind

DatasourceFieldInvalidError = _datasource_errors.DatasourceFieldInvalidError
DatasourceSecretInPlaintextError = _datasource_errors.DatasourceSecretInPlaintextError

RepairKind = Literal[
    "retry",
    "inspect",
    "user_choice",
    "semantic_authoring",
    "environment",
]


class AnalysisRepair(BaseModel):
    """Typed repair instruction for an :class:`AnalysisError`.

    Parameters
    ----------
    kind:
        Closed repair category. ``retry`` means the agent can re-attempt with
        a corrected call. ``inspect`` means the agent should gather more
        evidence before proceeding. ``user_choice`` means several mechanically
        legal repairs remain and business judgment must select one.
        ``semantic_authoring`` means a required semantic object is absent, so
        typed analysis must stop that branch; the agent may use terminal
        ``md.raw_sql(...)`` and must request semantic-authoring approval at
        closeout. ``environment`` means project or datasource state must be
        repaired before retry.
    action:
        One-sentence concrete next step.
    help_target:
        Canonical surface-qualified ``marivo.help(...)`` target to consult.
    snippet:
        Optional paste-ready code snippet.
    candidates:
        Optional tuple of live candidate strings (e.g. available metric ids).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: RepairKind
    action: str
    help_target: LiveHelpTarget
    snippet: str | None = None
    candidates: tuple[str, ...] = ()


class _DerivedFields(TypedDict, total=False):
    """Internal typed dict for derived stable fields.

    Keys are ``expected``, ``received``, ``location``, and ``repair``.
    """

    expected: str
    received: str
    location: str
    repair: AnalysisRepair


class AnalysisError(Exception):
    """Call marivo.help(AnalysisError) for its public consumption contract.

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
            target = self.repair.help_target
            qualified = (
                target.surface
                if target.canonical_id is None
                else f"{target.surface}.{target.canonical_id}"
            )
            lines.append(f"Help: marivo.help({qualified!r})")

        return "\n".join(lines)


class OntologyNotConfiguredError(AnalysisError):
    """Ontology-guided discovery was requested without authored ontology content."""

    @property
    def kind(self) -> str:
        return "ontology_not_configured"


class OntologyUnavailableError(AnalysisError):
    """The project ontology exists but is invalid for the current semantic catalog."""

    @property
    def kind(self) -> str:
        return "ontology_unavailable"


class MissingMetricLineageError(AnalysisError):
    """An admitted discovery source has no recoverable catalog Metric identity."""

    @property
    def kind(self) -> str:
        return "missing_metric_lineage"


class AmbiguousMetricLineageError(AnalysisError):
    """An admitted discovery source resolves to several catalog Metric identities."""

    @property
    def kind(self) -> str:
        return "ambiguous_metric_lineage"


class CandidateNotObservableError(AnalysisError):
    """A semantic-hypothesis candidate cannot be safely observed."""

    @property
    def kind(self) -> str:
        return "candidate_not_observable"


class CandidateScopeOverrideForbiddenError(AnalysisError):
    """Candidate observation attempted to replace its inherited scope."""

    @property
    def kind(self) -> str:
        return "candidate_scope_override_forbidden"


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
        structured_metric_ref = self._context.get("metric_ref")
        available = self._context.get("available_refs")
        metric_ref: str | None = None
        if type(structured_metric_ref) is Ref and structured_metric_ref.kind is SemanticKind.METRIC:
            metric_ref = structured_metric_ref.key
        elif isinstance(metric_id, str) and metric_id:
            metric_ref = metric_id
        elif isinstance(model, str) and model and isinstance(metric, str) and metric:
            metric_ref = f"{model}.{metric}"
        elif isinstance(metric, str) and metric:
            metric_ref = metric
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
                        'session.observe(catalog.require(ms.ref.metric("<registered_metric_id>")).ref, '
                        'time_scope=mv.time_scope(start="2026-07-01", end="2026-10-01"))'
                    ),
                    candidates=candidates,
                ),
            )
        # Absent case: typed analysis stops. Terminal raw SQL may continue with
        # temporary inferred semantics, but durable authoring waits for approval.
        return _DerivedFields(
            expected="registered metric semantic object",
            received=metric_ref,
            location="session.observe call",
            repair=AnalysisRepair(
                kind="semantic_authoring",
                action=(
                    f"metric_id={metric_ref} has no close match in the loaded "
                    "catalog; stop this typed branch. Terminal md.raw_sql(...) "
                    "may continue with explicit temporary assumptions; at closeout "
                    "request approval to author and register the metric before retrying."
                ),
                help_target=LiveHelpTarget(surface="semantic"),
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
                        'session.observe(session.catalog.require(ms.ref.metric("sales.revenue")), '
                        'time_scope=mv.time_scope(start="2026-07-01", end="2026-10-01"))'
                    )
                ),
                candidates=candidates,
            ),
        )


class TemporalSuitabilityError(WindowInvalidError):
    """Compiled semantic facts cannot support the requested temporal observation."""


class TimezoneInvalidError(AnalysisError):
    pass


class DataTypeMismatchError(AnalysisError):
    pass


class WindowAmbiguousError(AnalysisError): ...


class SliceInvalidError(AnalysisError): ...


class SliceEmptyResultError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        dimensions = self._context.get("slice_dimensions")
        if not isinstance(dimensions, (list, tuple)) or not dimensions:
            return _DerivedFields()
        return _DerivedFields(
            expected="a non-empty result for the requested slice_by",
            received="0 rows",
            location="session.observe call (slice_by)",
            repair=AnalysisRepair(
                kind="inspect",
                action=(
                    "Verify the slice_by values exist on the dimension and the "
                    "time_scope covers data; discover values via md.inspect(...)."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
                snippet=(
                    "import marivo.datasource as md\n"
                    "inspection = md.inspect(ms.ref.datasource('<name>'), "
                    "md.table('<entity_table>'))\n"
                    "inspection.sample(\n"
                    "    scope=md.unpruned(max_rows=100, timeout_seconds=60),\n"
                    "    columns=('<column>',),\n"
                    ").show()  # confirm the slice_by value and time_scope"
                ),
            ),
        )


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
                    action=(
                        "Pass a non-empty search_space with current catalog dimension "
                        "entries or exact refs."
                    ),
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="discover"),
                    snippet=(
                        'region = session.catalog.dimensions.get("sales.orders.region")\n'
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
        requested_item_id = self._context.get("item_id")
        match_count = self._context.get("match_count")
        if (
            isinstance(row_count, int)
            and isinstance(requested_item_id, str)
            and isinstance(match_count, int)
        ):
            return _DerivedFields(
                expected="one exact item_id from this CandidateSet",
                received=(
                    f"item_id={requested_item_id!r}, match_count={match_count}, "
                    f"row_count={row_count}"
                ),
                location="CandidateSet.select item_id argument",
                repair=AnalysisRepair(
                    kind="retry",
                    action="Copy one exact item_id rendered by CandidateSet.show().",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="discover"),
                    snippet=(
                        "candidates.show()\n"
                        'selection = candidates.select(item_id="candidate_<full sha256>")'
                    ),
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
        # Measure-rejection shape: a measure Ref was passed where a
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
                expected="dimension Ref or CatalogEntry",
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
            expected_type = self._context.get("expected_type")
            label = self._catalog_expected_label(argument, expected_kind_for_catalog)
            exact_type = (
                expected_type
                if isinstance(expected_type, str) and expected_type
                else f"Ref[{label}]"
            )
            cause = f"{argument} requires exact {exact_type}, received a {actual_kind_value}."
            available = self._context.get("available_refs")
            cause = _cause_with_available(cause, available)
            candidates = _candidates_preview(available)
            repair = self._context.get("repair")
            fix_snippet = (
                "\n".join(str(line) for line in repair)
                if isinstance(repair, list) and repair
                else None
            )
            return _DerivedFields(
                expected=exact_type,
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
                        "candidates = session.discover.point_anomalies(metric)\n"
                        "candidates.show()\n"
                        'selection = candidates.select(item_id="candidate_<full sha256>")'
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
                        'session.observe(session.catalog.require(ms.ref.metric("sales.revenue")), '
                        'time_scope=mv.time_scope(start="2026-07-01", end="2026-10-01"))'
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
                    'revenue = session.catalog.require(ms.ref.metric("sales.revenue"))\n'
                    'cur  = session.observe(revenue, time_scope=mv.time_scope(start="2026-07-01", end="2026-10-01"))\n'
                    'base = session.observe(revenue, time_scope=mv.time_scope(start="2025-07-01", end="2025-10-01"))\n'
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


class DiscoverAxisNotMaterializedError(AnalysisError):
    """Raised when a discover search_space references axes not materialized in the source frame.

    Fail-closed counterpart to the previous silent skip: a requested axis that
    is absent from the frame (wrong id, or forgotten in ``observe``) raises
    instead of being dropped to an indistinguishable empty CandidateSet.
    """

    def _derive_fields(self) -> _DerivedFields:
        objective = self._context.get("objective")
        missing = self._context.get("missing_axes")
        available = self._context.get("available_dimension_columns")
        objective_ref = objective if isinstance(objective, str) and objective else "discover"
        missing_ref = (
            ", ".join(str(axis) for axis in missing)
            if isinstance(missing, (list, tuple)) and missing
            else "<missing_axes>"
        )
        available_ref = (
            ", ".join(str(axis) for axis in available)
            if isinstance(available, (list, tuple)) and available
            else "<none>"
        )
        return _DerivedFields(
            expected="axes materialized as columns in the source frame",
            received=f"missing axes: {missing_ref}",
            location=f"session.discover.{objective_ref} search_space",
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    "re-observe the source with the requested dimensions, or pass only "
                    f"materialized axes. Available dimension columns: {available_ref}"
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="discover"),
                snippet=(
                    "frame = session.observe(metric, grain=mv.grain('day'), dimensions=[region, page])\n"
                    "session.discover.driver_axes(delta, search_space=[region, page])"
                ),
            ),
        )


class AlignmentPolicyValidationError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        case = self._context.get("case")
        if case == "direct_constructor":
            return _DerivedFields(
                location="mv.AlignmentPolicy(...)",
                repair=AnalysisRepair(
                    kind="retry",
                    action=(
                        "Construct an alignment with one closed helper: "
                        "mv.window_bucket(), mv.day_of_week(), mv.period_progress(), "
                        "mv.period_correspondence(), mv.occurrence_progress(), or "
                        "mv.working_day_progress(schedule=...)."
                    ),
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="alignment"),
                    snippet="alignment = mv.window_bucket()",
                ),
            )
        if case == "invalid_helper_arguments":
            helper = self._context.get("helper")
            helper_name = helper if isinstance(helper, str) else "mv.<alignment_helper>"
            return _DerivedFields(
                location=helper_name,
                repair=AnalysisRepair(
                    kind="retry",
                    action=f"Retry {helper_name} with only its documented arguments.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="alignment"),
                    snippet="marivo.help('analysis.alignment')",
                ),
            )
        return _DerivedFields(
            location="mv.alignment helper",
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
                    'revenue = session.catalog.require(ms.ref.metric("sales.revenue"))\n'
                    'cur = session.observe(revenue, time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01"), grain=mv.grain("day"))\n'
                    'base = session.observe(revenue, time_scope=mv.time_scope(start="2025-07-01", end="2025-08-01"), grain=mv.grain("day"))\n'
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
        case = self._context.get("case")
        period_binding = self._context.get("period_binding")
        semantic_binding = (
            isinstance(period_binding, dict) and period_binding.get("kind") == "semantic_period"
        )
        if (
            semantic_binding
            and isinstance(case, str)
            and (
                case.startswith("period_")
                or case
                in {
                    "panel_period_sequence_mismatch",
                    "period_columns_missing",
                    "period_history_empty",
                    "unsupported_model",
                    "seasonality_period_required",
                }
            )
        ):
            action = (
                "Re-observe the history with the same certified semantic grain and an exact "
                "snapshot; use only complete consecutive periods."
            )
            snippet = (
                'history = session.observe(session.catalog.require(ms.ref.metric("sales.revenue")), '
                'time_scope=mv.time_scope(start="2026-01-01", end="2026-04-01"), '
                'grain=session.catalog.period_calendars.get("sales.fiscal").grain("fiscal_week"))'
            )
            if case == "period_future_out_of_coverage":
                action = "Reduce horizon or certify a period-calendar snapshot covering the requested future periods."
            elif case == "period_snapshot_unavailable":
                action = "Re-certify the period calendar and re-observe history so its exact snapshot is available."
            elif case == "unsupported_model":
                action = "Use one admitted model: naive, drift, or seasonal_naive with an explicit seasonality_period."
            elif case == "seasonality_period_required":
                action = "Pass seasonality_period > 1 when using seasonal_naive on a semantic period grain."
            return _DerivedFields(
                location="session.forecast semantic period binding",
                repair=AnalysisRepair(
                    kind="retry",
                    action=action,
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="forecast"),
                    snippet=snippet,
                ),
            )
        return _DerivedFields(
            location="session.forecast input frame",
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    "forecast accepts MetricFrame time_series or panel shapes; for a semantic "
                    "grain, use one certified period binding with complete ordered history."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="forecast"),
                snippet=(
                    'history = session.observe(session.catalog.require(ms.ref.metric("sales.revenue")), time_scope=mv.time_scope(start="2026-01-01", end="2026-04-01"), grain=mv.grain("day"))\n'
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
                    'history = session.observe(session.catalog.require(ms.ref.metric("sales.revenue")), '
                    'time_scope=mv.time_scope(start="2026-01-01", end="2026-04-01"), grain=mv.grain("day"))'
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
            location="session.assess_quality frame",
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    "assess_quality accepts registered MetricFrame, EventFrame, "
                    "LifecycleFrame, DeltaFrame, and AttributionFrame shapes."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="assess_quality"),
                snippet="report = session.assess_quality(frame)",
            ),
        )


class MetricShapeUnsupportedError(AnalysisError):
    pass


class FrameMetaInvalidError(AnalysisError):
    """A persisted analysis frame's metadata is not usable as-is.

    Every construction site passes a typed ``repair`` explicitly (the class
    does not derive one from ``context`` — the raise sites are heterogeneous
    and the recovery intent differs per family), so an agent can see the
    concrete next step in ``str(e)``.
    """


class MetricArityError(AnalysisError):
    """An intent that requires a single-metric frame received a multi-metric frame."""


class TransformShapeUnsupportedError(AnalysisError):
    """Raised when an op requires axes the input frame does not have."""


class TransformArgError(AnalysisError):
    """Raised when transform kwargs are missing, wrong type, or contradict the op."""


class TransformDimensionNotFoundError(AnalysisError):
    """Raised when a where / drop_axes target is not present in frame axes."""


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
            raw_session_id = self._context.get("session_id")
            session_id = (
                raw_session_id
                if isinstance(raw_session_id, str) and raw_session_id
                else "<session-id>"
            )
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
                        "\n"
                        f"session_id = {session_id!r}\n"
                        'repair_choice = "<project-datasources-or-explicit-factory>"\n'
                        'if repair_choice == "project-datasources":\n'
                        "    # Register the real project datasource first, then resume.\n"
                        "    session = mv.session.resume(session_id)\n"
                        'elif repair_choice == "explicit-factory":\n'
                        "    import ibis\n"
                        "\n"
                        "    session = mv.session.resume(\n"
                        "        session_id,\n"
                        '        backend_factory=lambda name: ibis.duckdb.connect(":memory:"),\n'
                        "        use_datasources=False,\n"
                        "    )\n"
                        "else:\n"
                        "    raise ValueError(\n"
                        "        \"Set repair_choice to 'project-datasources' or 'explicit-factory'.\"\n"
                        "    )"
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
    """Private analysis-surface rejection adapted by unified help."""

    def __init__(
        self,
        *,
        target: object,
        suggestions: tuple[str, ...],
        owning_surface: Literal["datasource", "semantic"] | None = None,
    ) -> None:
        received = target if isinstance(target, str) else type(target).__name__
        message = "analysis help target is not registered"
        if owning_surface is not None:
            callable_target = getattr(target, "__func__", target)
            target_name = getattr(callable_target, "__qualname__", type(target).__qualname__)
            retry_call = f'marivo.help("{owning_surface}.{target_name}")'
            message += f". This target belongs to {owning_surface}; use {retry_call} instead."
            action = f"Retry with the owning surface: {retry_call}."
            help_target = LiveHelpTarget(
                surface=owning_surface,
                canonical_id=target_name,
            )
        else:
            action = "Use marivo.help() to browse registered targets."
            help_target = LiveHelpTarget(surface="analysis")
        if suggestions and owning_surface is None:
            # Surface fuzzy candidates on the first line. See issue #35.
            message += f". Did you mean: {', '.join(suggestions)}?"
        super().__init__(
            message=message,
            expected=(
                "None, canonical target string, registered public callable/type, "
                "public analysis object, semantic object/ref, or AnalysisError"
            ),
            received=str(received),
            location="marivo.help.target",
            repair=AnalysisRepair(
                kind="retry" if owning_surface is not None else "inspect",
                action=action,
                help_target=help_target,
                candidates=() if owning_surface is not None else suggestions,
            ),
        )


class DuplicateSessionNameError(AnalysisError): ...


class NoActiveSessionError(AnalysisError): ...


class SessionStateError(AnalysisError): ...


class SessionQuestionMismatchError(SessionStateError):
    """A session name is already bound to a different analysis question."""

    def _derive_fields(self) -> _DerivedFields:
        session_id = self._context.get("session_id", "<session-id>")
        persisted = self._context.get("persisted_question")
        requested = self._context.get("requested_question")
        return _DerivedFields(
            expected=f"question={persisted!r}",
            received=f"question={requested!r}",
            location="mv.session.get_or_create(question=...)",
            repair=AnalysisRepair(
                kind="user_choice",
                action=(
                    "Resume the existing session by id, or choose a new stable "
                    "session name for the requested question."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="recovery"),
                snippet=(
                    'repair_choice = "<resume-existing-or-create-new>"\n'
                    'if repair_choice == "resume-existing":\n'
                    f"    session = mv.session.resume({session_id!r})\n"
                    'elif repair_choice == "create-new":\n'
                    "    session = mv.session.get_or_create(\n"
                    '        "<new-stable-session-name>",\n'
                    f"        question={requested!r},\n"
                    "    )\n"
                    "else:\n"
                    "    raise ValueError(\n"
                    "        \"Set repair_choice to 'resume-existing' or 'create-new'.\"\n"
                    "    )"
                ),
            ),
        )


class SourceBindingError(SessionStateError): ...


class SessionNotFoundError(SessionStateError): ...


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
                        'session.observe(catalog.require(ms.ref.metric("sales.revenue")), '
                        'dimensions=[catalog.require(ms.ref.dimension("<existing_dimension>")).ref])'
                    ),
                    candidates=candidates,
                ),
            )
        # No close matches: typed slicing stops. A terminal raw SQL branch may
        # continue, but durable semantic authoring remains approval-gated.
        return _DerivedFields(
            expected="dimension or time dimension on the metric's datasets",
            received=dim_ref,
            location="session.observe dimensions argument",
            repair=AnalysisRepair(
                kind="semantic_authoring",
                action=(
                    f"dimension {dim_ref!r} has no close match on the metric's "
                    "datasets; stop this typed branch. Terminal md.raw_sql(...) "
                    "may continue with explicit temporary assumptions; at closeout "
                    "request approval to author and register the dimension before retrying."
                ),
                help_target=LiveHelpTarget(surface="semantic"),
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
                    'axis = session.catalog.require(ms.ref.dimension("<domain.entity.dimension>")).ref\n'
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
                    "metric = session.catalog.require(ms.ref.metric('model.metric'))\n"
                    'common_dim = session.catalog.require(ms.ref.dimension("model.entity.common_dim")).ref\n'
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


class SessionLockedByAnotherProcessError(AnalysisError): ...


class FindingNotFoundError(AnalysisError): ...


class EvidenceDigestNotAvailableError(AnalysisError): ...


class EvidenceSelectionError(AnalysisError):
    """A compatibility selection is empty, duplicated, oversized, or malformed."""


class EvidenceIntegrityError(AnalysisError):
    """Committed evidence cannot be resolved to one intact canonical graph."""


class ArtifactStaleError(AnalysisError):
    """An operator requires current semantic authority for a stale Artifact."""

    @property
    def kind(self) -> str:
        return "artifact_stale"


class ArtifactAuthorityUnknownError(AnalysisError):
    """An operator cannot establish required current Artifact authority."""

    @property
    def kind(self) -> str:
        return "artifact_authority_unknown"


class CumulativeFrameUnsupportedError(AnalysisError):
    """Intent received a cumulative frame outside the supported intent boundary."""

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
        compare_blocker = (
            cumulative_compare_blocker(cumulative)
            if cumulative.get("kind") == "derived_contains_cumulative"
            else cumulative.get("compare_blocker")
        )
        if base is None and isinstance(components, dict):
            base = ", ".join(
                sorted(
                    str(payload.get("base"))
                    for payload in components.values()
                    if isinstance(payload, dict)
                )
            )
        anchor = cumulative.get("anchor")
        if intent == "compare" and isinstance(compare_blocker, str):
            hint = (
                f"Derived cumulative compare is blocked by {compare_blocker!r}. Every outer "
                "component must be cumulative and share one supported anchor."
            )
        elif intent == "compare" and anchor == "all_history":
            hint = (
                "Re-observe both all-history cumulative frames so every row carries the "
                "current evaluation_end contract."
            )
        elif intent == "compare":
            hint = "Re-observe both frames with one compatible cumulative anchor."
        elif intent in {"attribute", "decompose"}:
            hint = (
                f"{intent} is unsupported for direct and derived cumulative deltas. "
                "Use the underlying flow metrics separately."
            )
        elif intent == "forecast":
            hint = "Forecast the base flow metric instead of a cumulative frame."
        else:
            hint = "Use the underlying flow metric for this intent."
        super().__init__(
            message=f"{intent} does not support cumulative metric frames.",
            hint=hint,
            context={
                "intent": intent,
                "frame_ref": frame_ref,
                "metric_id": metric_id,
                "base_metric_id": base,
                "compare_blocker": compare_blocker,
                "cumulative_anchor": anchor,
                "cumulative": dict(cumulative),
            },
        )

    def _derive_fields(self) -> _DerivedFields:
        intent = self._context.get("intent")
        intent_str = intent if isinstance(intent, str) and intent else "<intent>"
        base = self._context.get("base_metric_id")
        base_str = base if isinstance(base, str) and base else None
        blocker = self._context.get("compare_blocker")
        anchor = self._context.get("cumulative_anchor")
        if intent_str in {"attribute", "decompose"}:
            action = (
                f"Use the underlying flow metrics separately; {intent_str} is unsupported "
                "for cumulative deltas."
            )
        elif intent_str == "compare" and isinstance(blocker, str):
            action = (
                f"Resolve {blocker!r}: every outer component must be cumulative and share "
                "one supported anchor, then re-observe both frames."
            )
        elif intent_str == "compare" and anchor == "all_history":
            action = "Re-observe both frames so every row carries evaluation_end."
        else:
            metric = f" ({base_str})" if base_str is not None else ""
            action = (
                f"Re-observe the underlying flow metric{metric} and retry {intent_str} "
                "on that frame."
            )
        return _DerivedFields(
            expected="a cumulative frame supported by the selected intent",
            received="cumulative metric frame",
            location=f"session.{intent_str}",
            repair=AnalysisRepair(
                kind="retry",
                action=action,
                help_target=LiveHelpTarget(surface="analysis", canonical_id=intent_str),
            ),
        )


class SemanticCumulativeBucketCompareUnsupportedError(AnalysisError):
    """Bucketed (time-series/panel) cumulative compare with a semantic calendar query grain."""

    def __init__(self, *, calendar_ref: str, level: str, frame_ref: str) -> None:
        super().__init__(
            message=(
                "bucketed (time-series/panel) cumulative compare does not support semantic "
                f"calendar query grains (got {calendar_ref}:{level})."
            ),
            location="session.compare",
            hint=(
                "Panel/time-series semantic-calendar cumulative compare is not supported. "
                "Compare the scalar cumulative frames (no query grain) with "
                "alignment=mv.period_progress(), or decompose the underlying base flow metric."
            ),
            context={
                "kind": "SemanticCumulativeBucketCompareUnsupported",
                "calendar_ref": calendar_ref,
                "level": level,
                "frame_ref": frame_ref,
            },
        )

    def _derive_fields(self) -> _DerivedFields:
        calendar_ref = self._context.get("calendar_ref")
        level = self._context.get("level")
        calendar_ref_str = calendar_ref if isinstance(calendar_ref, str) else "<calendar>"
        level_str = level if isinstance(level, str) else "<level>"
        return _DerivedFields(
            location="session.compare",
            repair=AnalysisRepair(
                kind="user_choice",
                action=(
                    f"Semantic-calendar bucketed cumulative compare ({calendar_ref_str}:"
                    f"{level_str}) is unsupported. Compare scalar cumulative frames with "
                    "alignment=mv.period_progress(), or attribute the base flow metric directly."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
            ),
        )


class ComponentFrameUnavailableError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        loaded_kind = self._context.get("loaded_kind")
        component_ref = self._context.get("component_ref")
        composition = self._context.get("composition")
        parent_kind = self._context.get("parent_kind")
        producer = "compare" if parent_kind == "delta_frame" else "observe"
        regeneration_snippet = (
            "delta = session.compare(current, baseline)\ncomponents = delta.components()"
            if parent_kind == "delta_frame"
            else (
                "frame = session.observe(session.catalog.require("
                'ms.ref.metric("model.derived_ratio")))\ncomponents = frame.components()'
            )
        )
        if isinstance(loaded_kind, str):
            # A ref that resolves to a non-ComponentFrame: the parent's saved
            # pointer and the on-disk frame disagree — the artifact relationship
            # is damaged.
            return _DerivedFields(
                location="frame.components()",
                repair=AnalysisRepair(
                    kind="environment",
                    action=(
                        "component_ref resolved to the wrong frame kind; the "
                        "metric/component artifact relationship is damaged. Re-run "
                        f"{producer}() to regenerate the frame and its component "
                        "sidecar together."
                    ),
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="artifacts"),
                    snippet=regeneration_snippet,
                ),
            )
        if component_ref is not None:
            # A ref that no longer resolves on disk: the sidecar was not
            # persisted or was deleted. Re-running observe() rebuilds it.
            return _DerivedFields(
                location="frame.components()",
                repair=AnalysisRepair(
                    kind="retry",
                    action=(
                        "component_ref points at a sidecar that is missing on "
                        f"disk. Re-run {producer}() to regenerate the frame "
                        "and its component sidecar."
                    ),
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="artifacts"),
                    snippet=regeneration_snippet,
                ),
            )
        if isinstance(composition, dict):
            # composition declares the frame is decomposable but no sidecar was
            # persisted — an incomplete write, not an ordinary scalar frame.
            return _DerivedFields(
                location="frame.components()",
                repair=AnalysisRepair(
                    kind="environment",
                    action=(
                        "The frame declares a component composition but no "
                        "component sidecar was persisted — an incomplete write. "
                        f"Re-run {producer}() to regenerate the frame and its sidecar."
                    ),
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="artifacts"),
                ),
            )
        action = (
            "Component frames are only available for component-aware deltas produced by compare."
            if parent_kind == "delta_frame"
            else (
                "Component frames are only available for derived ratio or weighted-mean "
                "frames produced by component-aware observe/compare."
            )
        )
        return _DerivedFields(
            location="frame.components()",
            repair=AnalysisRepair(
                kind="inspect",
                action=action,
                help_target=LiveHelpTarget(surface="analysis", canonical_id="artifacts"),
                snippet=regeneration_snippet,
            ),
        )


class ComponentFrameMismatchError(AnalysisError):
    pass


class ComponentDecompositionError(AnalysisError):
    pass


class AttributionAdditivityError(ComponentDecompositionError):
    """A delta cannot be attributed with additive axis-sum math."""


class AttributionMaterializationError(AnalysisError):
    def _derive_fields(self) -> _DerivedFields:
        if self._context.get("recoverability_status") == "semantic_grain_decomposition_unsupported":
            return _DerivedFields(
                location="session.attribute cumulative semantic-grain decomposition",
                repair=AnalysisRepair(
                    kind="inspect",
                    action=(
                        "Semantic-calendar cumulative deltas cannot be decomposed. "
                        "Attribute the underlying base flow metric directly, or re-observe "
                        "the metric with a builtin calendar reset grain."
                    ),
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
                ),
            )
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


class AttributionBasisMismatchError(AnalysisError):
    """Persisted attribution bases or source graphs are incompatible."""

    def _derive_fields(self) -> _DerivedFields:
        return _DerivedFields(
            location="session.attribute attribution basis",
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    "Re-run observe and compare from the current semantic graph, then retry "
                    "attribute with the newly persisted DeltaFrame."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
            ),
        )

    @property
    def kind(self) -> str:
        return "attribution_basis_mismatch"


class AttributionShapeUnavailableError(AnalysisError):
    """No closed mathematical attribution shape can be projected."""

    def _derive_fields(self) -> _DerivedFields:
        return _DerivedFields(
            location="DeltaFrame.predicted_attribution_shape()",
            repair=AnalysisRepair(
                kind="inspect",
                action=(
                    "Inspect DeltaFrame.contract().attribute_admission and use its typed "
                    "repair before attempting attribution."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
            ),
        )

    @property
    def kind(self) -> str:
        return "attribution_shape_unavailable"


class AttributeAdmissionBlockedError(AnalysisError):
    """The effective installed-runtime attribution admission is blocked."""

    def _derive_fields(self) -> _DerivedFields:
        return _DerivedFields(
            location="session.attribute",
            repair=AnalysisRepair(
                kind="inspect",
                action=(
                    "Inspect DeltaFrame.contract().attribute_admission and apply its typed "
                    "repair before retrying attribute."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
            ),
        )

    @property
    def kind(self) -> str:
        return "attribute_admission_blocked"


class AttributionResolutionError(AnalysisError):
    """A requested multi-resolution axis prefix is not available."""

    def _derive_fields(self) -> _DerivedFields:
        return _DerivedFields(
            location="AttributionFrame.at_resolution",
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    "Choose one exact ordered semantic-ref prefix from frame.contract(), "
                    "then call frame.at_resolution(axes=[...]) again."
                ),
                help_target=LiveHelpTarget(
                    surface="analysis", canonical_id="AttributionFrame.at_resolution"
                ),
            ),
        )

    @property
    def kind(self) -> str:
        return "attribution_resolution_invalid"


class AttributionDistributionError(AnalysisError):
    """Distribution attribution cannot safely evaluate its governed game."""

    def _derive_fields(self) -> _DerivedFields:
        reason = self._context.get("reason")
        if reason == "empty_coalition_distribution":
            kind: RepairKind = "retry"
            action = (
                "Retry attribute with a coarser axis or overlapping partitions so every "
                "evaluated coalition has a non-null distribution."
            )
        elif reason in {"partition_limit_exceeded", "frequency_row_limit_exceeded"}:
            kind = "retry"
            action = (
                "Retry attribute with a coarser or lower-cardinality axis so the governed "
                "distribution game stays within its execution limit."
            )
        elif reason == "endpoint_reproduction_mismatch":
            kind = "inspect"
            action = (
                "Re-run observe and compare against the active datasource, then inspect the "
                "persisted attribution basis before retrying attribute."
            )
        else:
            kind = "inspect"
            action = (
                "Inspect DeltaFrame.contract().attribute_admission and the persisted source "
                "method before retrying distribution attribution."
            )
        return _DerivedFields(
            location="session.attribute distribution evaluation",
            repair=AnalysisRepair(
                kind=kind,
                action=action,
                help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
            ),
        )

    @property
    def kind(self) -> str:
        reason = self._context.get("reason")
        return str(reason) if isinstance(reason, str) and reason else "attribution_distribution"


class InvalidEventPatternError(AnalysisError):
    @property
    def kind(self) -> str:
        return "invalid_event_pattern"


class PatternStepMismatchError(AnalysisError):
    @property
    def kind(self) -> str:
        return "pattern_step_mismatch"


class InvalidEventMatchingPolicyError(AnalysisError):
    @property
    def kind(self) -> str:
        return "invalid_event_matching_policy"


class InvalidCompletenessDeclarationError(AnalysisError):
    @property
    def kind(self) -> str:
        return "invalid_completeness_declaration"


class AmbiguousEventOrderError(AnalysisError):
    @property
    def kind(self) -> str:
        return "ambiguous_event_order"


class InsufficientStateHistoryError(AnalysisError):
    """Replay cannot establish a subject state from proven-complete history."""

    @property
    def kind(self) -> str:
        return "insufficient_state_history"


class InvalidLifecycleSeedError(AnalysisError):
    """Lifecycle replay received a seed outside its closed phase contract."""

    @property
    def kind(self) -> str:
        return "invalid_lifecycle_seed"


class ModelStateMismatchError(AnalysisError):
    """A ModelStateHandle is incompatible with the active Lifecycle history."""

    @property
    def kind(self) -> str:
        return "model_state_mismatch"


class InvalidDistributionInstantsError(AnalysisError):
    """Lifecycle distribution instants violate the source history window."""

    @property
    def kind(self) -> str:
        return "invalid_distribution_instants"


class EventIdentityError(AnalysisError):
    @property
    def kind(self) -> str:
        return "invalid_event_identity"


class EventParticipantCardinalityError(AnalysisError):
    @property
    def kind(self) -> str:
        return "invalid_event_participant_cardinality"


class InvalidSubjectAxisError(AnalysisError):
    """A requested Event reducer axis is not a governed subject axis."""

    @property
    def kind(self) -> str:
        return "invalid_subject_axis"


class GroupedReconciliationFailedError(AnalysisError):
    """Grouped Event counts failed exact reconciliation to the ungrouped source."""

    @property
    def kind(self) -> str:
        return "grouped_reconciliation_failed"


class EventCoverageUnknownError(AnalysisError):
    """A continuation requires subject truth that is coverage-censored."""

    @property
    def kind(self) -> str:
        return "event_coverage_unknown"


class SubjectSetMismatchError(AnalysisError):
    """A SubjectSet is incompatible with the requested typed consumer."""

    @property
    def kind(self) -> str:
        return "subject_set_mismatch"


class FunnelComparisonMismatchError(AnalysisError):
    """Two funnels are structurally incompatible for exact comparison."""

    @property
    def kind(self) -> str:
        return "funnel_comparison_mismatch"


class FunnelAttributionUnsupportedError(AnalysisError):
    """A funnel delta cannot support the requested attribution."""

    @property
    def kind(self) -> str:
        return "funnel_attribution_unsupported"
