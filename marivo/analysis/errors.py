"""Structured error hierarchy for marivo.analysis."""

from __future__ import annotations

from typing import Any

from marivo.datasource import errors as _datasource_errors

DatasourceFieldInvalidError = _datasource_errors.DatasourceFieldInvalidError
DatasourceSecretInPlaintextError = _datasource_errors.DatasourceSecretInPlaintextError


class AnalysisError(Exception):
    """Base class for all analysis errors."""

    def __init__(
        self,
        *,
        message: str,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
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

    def _template_fields(self) -> dict[str, str]:
        return {}

    def _resolved_field(self, key: str) -> str | None:
        detail_value = self.details.get(key)
        if isinstance(detail_value, str) and detail_value:
            return detail_value
        template_value = self._template_fields().get(key)
        if isinstance(template_value, str) and template_value:
            return template_value
        return None

    def __str__(self) -> str:
        lines = [f"{type(self).__name__}: {self.message}"]

        context_lines = []
        if location := self._resolved_field("location"):
            context_lines.append(f"Location: {location}")
        if cause := self._resolved_field("cause"):
            context_lines.append(f"Cause: {cause}")
        if self.hint:
            context_lines.append(f"Hint: {self.hint}")
        if context_lines:
            lines.append("")
            lines.extend(context_lines)

        if fix_snippet := self._resolved_field("fix_snippet"):
            lines.append("")
            lines.append("Fix:")
            lines.extend(f"  {line}" for line in fix_snippet.splitlines())

        if doc := self._resolved_field("doc"):
            lines.append("")
            lines.append(f"Docs: {doc}")

        return "\n".join(lines)


class ReportPublishError(AnalysisError):
    """Base class for report publishing failures."""


class ReportPublishConfigError(ReportPublishError):
    """No publish target could be resolved from arguments, env, or config files."""


class ReportPublishValidationError(ReportPublishError):
    """A staged report package failed publish-time validation."""


class ReportPublishTargetExistsError(ReportPublishError):
    """The publish destination already has a completed manifest."""


class ReportPublishAttributionError(ReportPublishError):
    """Exporter attribution is missing or does not match the publish path."""


class GrainUnsupportedError(AnalysisError):
    """A requested analysis grain is incompatible with the time field base granularity."""


class MetricNotFoundError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        metric_id = self.details.get("metric_id")
        model = self.details.get("model")
        metric = self.details.get("metric")
        available = self.details.get("available_ids")
        metric_ref = None
        if isinstance(metric_id, str) and metric_id:
            metric_ref = metric_id
        elif isinstance(model, str) and model and isinstance(metric, str) and metric:
            metric_ref = f"{model}.{metric}"
        if not metric_ref:
            return {"location": "session.observe call"}
        cause = f"metric_id={metric_ref} is not registered in the active semantic model."
        if isinstance(available, list) and available:
            preview = ", ".join(str(item) for item in available[:10])
            suffix = f" Available metrics: {preview}"
            if len(available) > 10:
                suffix += f" (+{len(available) - 10} more)"
            cause += suffix
        return {
            "location": "session.observe call",
            "cause": cause,
            "fix_snippet": (
                "import marivo.semantic as ms\n"
                "catalog = ms.load()\n"
                "catalog.list(kind='metric')  # confirm the exact id\n"
                'session.observe(catalog.get("<registered_metric_id>"), '
                'timescope={"start": "2026-07-01", "end": "2026-10-01"})'
            ),
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class WindowInvalidError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        window = self.details.get("window") or self.details.get("timescope")
        window_ref = window if isinstance(window, str) and window else "<timescope>"
        return {
            "location": "session.observe timescope or frame window argument",
            "cause": f"timescope={window_ref} could not be parsed.",
            "fix_snippet": (
                'session.observe(session.catalog.get("sales.revenue"), '
                'timescope={"start": "2026-07-01", "end": "2026-10-01"})'
            ),
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class TimezoneInvalidError(AnalysisError):
    pass


class DataTypeMismatchError(AnalysisError):
    pass


class WindowAmbiguousError(AnalysisError): ...


class SliceInvalidError(AnalysisError): ...


class SliceAmbiguousError(AnalysisError): ...


class SemanticKindMismatchError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        if str(self.details.get("missing")) == "search_space":
            return {
                "location": "session.discover.driver_axes arguments",
                "cause": (
                    "discover.driver_axes requires a non-empty "
                    "search_space=[catalog dimension refs]."
                ),
                "fix_snippet": (
                    'region = session.catalog.get("sales.orders.region").ref\n'
                    "session.discover.driver_axes(delta, search_space=[region])"
                ),
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        got_semantic_shape = self.details.get("got_semantic_shape")
        expected_semantic_shape = self.details.get("expected_semantic_shape")
        if isinstance(got_semantic_shape, str) and isinstance(expected_semantic_shape, str):
            frame_kind = self.details.get("frame_kind")
            frame_ref = frame_kind if isinstance(frame_kind, str) and frame_kind else "frame"
            return {
                "location": f"{frame_ref}.as_{expected_semantic_shape}() narrowing",
                "cause": (
                    f"semantic_shape is {got_semantic_shape!r}, expected "
                    f"{expected_semantic_shape!r}; as_{expected_semantic_shape}() is only "
                    f"valid on a {expected_semantic_shape} frame."
                ),
                "fix_snippet": (
                    f'if frame.semantic_shape == "{expected_semantic_shape}":\n'
                    f"    typed = frame.as_{expected_semantic_shape}()"
                ),
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        intent = self.details.get("intent")
        predicted_semantic_shape = self.details.get("predicted_semantic_shape")
        expect_shape = self.details.get("expect_shape")
        if (
            isinstance(intent, str)
            and isinstance(predicted_semantic_shape, str)
            and isinstance(expect_shape, str)
        ):
            return {
                "location": f"session.{intent}(expect_shape=...) guard",
                "cause": (
                    f"{intent} will produce semantic_shape {predicted_semantic_shape!r} "
                    f"for these inputs, but expect_shape={expect_shape!r} was requested."
                ),
                "fix_snippet": (
                    f'frame = session.{intent}(metric, expect_shape="{predicted_semantic_shape}")'
                ),
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        got_attribution_shape = self.details.get("got_attribution_shape")
        expected_attribution_shape = self.details.get("expected_attribution_shape")
        if isinstance(got_attribution_shape, str) and isinstance(expected_attribution_shape, str):
            frame_kind = self.details.get("frame_kind")
            frame_ref = frame_kind if isinstance(frame_kind, str) and frame_kind else "frame"
            return {
                "location": f"{frame_ref}.as_{expected_attribution_shape}() narrowing",
                "cause": (
                    f"attribution_shape is {got_attribution_shape!r}, expected "
                    f"{expected_attribution_shape!r}; as_{expected_attribution_shape}() is only "
                    f"valid on a {expected_attribution_shape} attribution frame."
                ),
                "fix_snippet": (
                    f'if frame.attribution_shape == "{expected_attribution_shape}":\n'
                    f"    typed = frame.as_{expected_attribution_shape}()"
                ),
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        got_shape = self.details.get("got_shape")
        expected_shape = self.details.get("expected_shape")
        if isinstance(got_shape, str) and isinstance(expected_shape, str):
            return {
                "location": "CandidateSet.as_<shape>() narrowing",
                "cause": (
                    f"CandidateSet.shape is {got_shape!r}, expected {expected_shape!r}; "
                    f"as_{expected_shape}() is only valid on a {expected_shape} candidate set."
                ),
                "fix_snippet": (
                    'if cands.meta.shape == "' + str(expected_shape) + '":\n'
                    "    typed = cands.as_" + str(expected_shape) + "()"
                ),
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        row_count = self.details.get("row_count")
        requested_rank = self.details.get("requested_rank")
        if isinstance(row_count, int) and isinstance(requested_rank, int):
            return {
                "location": "CandidateSet.select rank argument",
                "cause": (
                    f"select(rank={requested_rank}) is out of range; the candidate set has "
                    f"{row_count} row(s)."
                ),
                "fix_snippet": (
                    "if cands.meta.row_count >= 1:\n"
                    '    value = cands.select(rank=1, attribute="...")'
                ),
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        shape = self.details.get("shape")
        attribute = self.details.get("attribute")
        valid_fields = self.details.get("valid_fields")
        if isinstance(shape, str) and isinstance(attribute, str):
            valid_list = (
                ", ".join(sorted(valid_fields))
                if isinstance(valid_fields, list) and valid_fields
                else None
            )
            cause = (
                f"select(attribute={attribute!r}) is not available on a CandidateSet[{shape}]; "
                "see the attribute-by-shape matrix in SKILL.md."
            )
            if valid_list:
                cause += f" Valid attributes for shape {shape!r}: {valid_list}."
            first_valid = (
                sorted(valid_fields)[0]
                if isinstance(valid_fields, list) and valid_fields
                else "score"
            )
            return {
                "location": "CandidateSet.select attribute argument",
                "cause": cause,
                "fix_snippet": (f'value = cands.select(rank=1, attribute="{first_valid}")'),
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        objective = self.details.get("objective")
        source_kind_value = self.details.get("source_kind")
        semantic_kind_value = self.details.get("semantic_kind")
        expected_kind_raw = self.details.get("expected_kind")
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
            return {
                "location": "session.discover dispatch",
                "cause": (
                    f"discover objective {objective!r} does not accept "
                    f"semantic_kind {semantic_kind_value!r} on a {source_kind_value}; "
                    f"allowed semantic_kinds: {expected_kind_str}."
                ),
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        if isinstance(objective, str) and isinstance(source_kind_value, str):
            return {
                "location": "session.discover dispatch",
                "cause": (
                    f"discover objective {objective!r} does not accept source kind "
                    f"{source_kind_value!r}; allowed source kinds: {expected_kind_str}."
                ),
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        if expected_kind_raw == "implemented_objective":
            return {
                "location": "session.discover dispatch",
                "cause": (
                    f"discover objective {objective!r} is not yet implemented in this build."
                ),
                "doc": "docs/specs/analysis/python-analysis-operator-design.md",
            }
        got_kind = self.details.get("got_kind")
        expected_kind = self.details.get("expected_kind")
        if not (
            isinstance(got_kind, str)
            and got_kind
            and isinstance(expected_kind, str)
            and expected_kind
        ):
            return {
                "cause": "Input frame kind or value shape does not match the requested analysis operation.",
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        if expected_kind == "candidate_set":
            return {
                "location": "CandidateSet.select call",
                "cause": (
                    f"got kind {got_kind}, expected {expected_kind}; CandidateSet.select only "
                    "operates on CandidateSet artifacts."
                ),
                "fix_snippet": (
                    "cands = session.discover.point_anomalies(metric)\n"
                    'window = cands.select(rank=1, attribute="window")'
                ),
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        if expected_kind == "metric":
            return {
                "location": "session.observe call",
                "cause": (
                    f"got {got_kind}, expected {expected_kind}; observe requires "
                    "a catalog metric object or ref."
                ),
                "fix_snippet": (
                    'session.observe(session.catalog.get("sales.revenue"), '
                    'timescope={"start": "2026-07-01", "end": "2026-10-01"})'
                ),
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        if got_kind != "delta_frame" or expected_kind != "metric_frame":
            return {
                "cause": (
                    f"got kind {got_kind}, expected {expected_kind}; input frame kind does not "
                    "match the requested analysis operation."
                ),
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        return {
            "location": "session.compare call",
            "cause": (
                f"got kind {got_kind}, expected {expected_kind}; this usually means passing a "
                "compare result where an observe result is required."
            ),
            "fix_snippet": (
                'revenue = session.catalog.get("sales.revenue")\n'
                "cur  = session.observe(revenue, "
                'timescope={"start": "2026-07-01", "end": "2026-10-01"})\n'
                "base = session.observe(revenue, "
                'timescope={"start": "2025-07-01", "end": "2025-10-01"})\n'
                'delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))'
            ),
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class AlignmentFailedError(AnalysisError): ...


class DiscoverInsufficientDataError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        objective = self.details.get("objective")
        row_count = self.details.get("row_count")
        minimum = self.details.get("minimum")
        objective_ref = objective if isinstance(objective, str) and objective else "period_shifts"
        count_ref = row_count if isinstance(row_count, int) else "<row_count>"
        minimum_ref = minimum if isinstance(minimum, int) else 4
        return {
            "location": "session.discover.period_shifts input",
            "cause": (
                f"discover objective {objective_ref!r} needs at least {minimum_ref} "
                f"time buckets in one series; got {count_ref} usable bucket(s)."
            ),
            "fix_snippet": (
                'delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))\n'
                'session.discover.period_shifts(delta, value="delta")  # use a wider window'
            ),
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class AlignmentPolicyValidationError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        case = self.details.get("case")
        kind = self.details.get("kind")
        if case == "missing_calendar":
            kind_str = kind if isinstance(kind, str) and kind else "dow_aligned"
            return {
                "location": "mv.AlignmentPolicy(...)",
                "cause": (
                    f"alignment kind {kind_str!r} requires a calendar; calendar-backed "
                    "alignment cannot resolve buckets without one."
                ),
                "fix_snippet": (
                    f'mv.AlignmentPolicy(kind="{kind_str}",\n'
                    '                   calendar=mv.CalendarRef("cn_holidays"),\n'
                    '                   period="month")'
                ),
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        if case == "legacy_calendar_bucket":
            return {
                "location": "mv.AlignmentPolicy(...)",
                "cause": (
                    "alignment kind 'calendar_bucket' was renamed; use 'window_bucket' "
                    "for request-window bucket spine alignment."
                ),
                "fix_snippet": 'mv.AlignmentPolicy(kind="window_bucket")',
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        if case == "unexpected_calendar":
            return {
                "location": "mv.AlignmentPolicy(...)",
                "cause": (
                    "window_bucket alignment infers buckets from the input windows and does not "
                    "accept a calendar argument."
                ),
                "fix_snippet": 'mv.AlignmentPolicy(kind="window_bucket")  # no calendar argument',
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        return {
            "location": "mv.AlignmentPolicy(...)",
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class PromotionFailedError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        target_kind = self.details.get("target_kind")
        missing = self.details.get("missing")
        target = target_kind if isinstance(target_kind, str) and target_kind else "frame"
        if isinstance(missing, list) and missing:
            missing_fields = {str(field) for field in missing}
            return {
                "location": f"session.promote_{target}",
                "cause": f"promotion is missing required metadata: {', '.join(map(str, missing))}.",
                "fix_snippet": (
                    "session.promote_metric_frame(\n"
                    "    scratch,\n"
                    '    metric=session.catalog.get("sales.revenue"),\n'
                    '    semantic_kind="segmented",\n'
                    '    measure_column="value",\n'
                    '    axes={"country": session.catalog.get("sales.orders.country").ref},\n'
                    '    semantic_model="sales",\n'
                    ")"
                )
                if missing_fields & {"metric", "measure_column", "semantic_model"}
                else "Pass the missing typed ref or column name shown in error details.",
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        ambiguous = self.details.get("ambiguous")
        catalog_misses = [
            str(item).removeprefix("metric_not_in_catalog:")
            for item in (ambiguous if isinstance(ambiguous, list) else [])
            if str(item).startswith("metric_not_in_catalog:")
        ]
        if catalog_misses:
            return {
                "location": f"session.promote_{target}",
                "cause": (
                    f"metric '{catalog_misses[0]}' is not defined in the loaded "
                    "semantic catalog; see available_metric_ids in error details."
                ),
                "fix_snippet": (
                    "import marivo.semantic as ms\n"
                    "catalog = ms.load()\n"
                    'catalog.list(kind="metric").show()  # pick a defined metric id, then re-promote'
                ),
                "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
            }
        return {
            "location": f"session.promote_{target}",
            "cause": "promotion metadata is incomplete or ambiguous.",
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class TestShapeNotTestableError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.hypothesis_test call",
            "cause": "mean_changed needs paired observations; scalar frames or too-small paired samples cannot be tested in v1.",
            "fix_snippet": (
                'revenue = session.catalog.get("sales.revenue")\n'
                'cur = session.observe(revenue, timescope={"start": "2026-07-01", "end": "2026-08-01"}, grain="day")\n'
                'base = session.observe(revenue, timescope={"start": "2025-07-01", "end": "2025-08-01"}, grain="day")\n'
                "session.hypothesis_test(cur, base)"
            ),
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class TestPolicyError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.hypothesis_test policy arguments",
            "cause": "test v1 only supports mean_changed, window_bucket alignment, and shape-compatible SamplingPolicy.pairing.",
            "fix_snippet": "session.hypothesis_test(cur, base, sampling=mv.SamplingPolicy(pairing='window_bucket'), alpha=0.05)",
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class TestAlignmentError(AlignmentFailedError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.hypothesis_test alignment",
            "cause": "the input frames did not produce any paired samples after alignment and null dropping.",
            "fix_snippet": "session.hypothesis_test(cur, base, alignment=mv.AlignmentPolicy(kind='window_bucket'))",
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class ForecastShapeUnsupportedError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.forecast input frame",
            "cause": "forecast v1 accepts only MetricFrame time_series or panel shapes.",
            "fix_snippet": 'history = session.observe(session.catalog.get("sales.revenue"), timescope={"start": "2026-01-01", "end": "2026-04-01"}, grain="day")\nsession.forecast(history, horizon=30)',
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class ForecastPolicyError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.forecast policy arguments",
            "cause": "horizon, interval_level, model, seasonality_period, or grain is outside the v1 supported contract.",
            "fix_snippet": "session.forecast(history, horizon=30, model='seasonal_naive', seasonality_period=7, interval_level=0.95)",
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class ForecastInsufficientHistoryError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.forecast history",
            "cause": "the time_series input has fewer training points than the selected model requires.",
            "fix_snippet": 'history = session.observe(session.catalog.get("sales.revenue"), timescope={"start": "2026-01-01", "end": "2026-04-01"}, grain="day")',
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class ForecastInputQualityError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.forecast history data",
            "cause": "forecast does not silently impute NaN values or fill missing time buckets.",
            "fix_snippet": "clean = session.transform.window(history, window={...})  # or impute upstream before forecasting",
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class QualityShapeUnsupportedError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.assess_quality target",
            "cause": "assess_quality v1 only supports MetricFrame targets; other frame families are planned for v1.1+.",
            "fix_snippet": "report = session.assess_quality(metric_frame)",
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class MetricShapeUnsupportedError(AnalysisError):
    pass


class FrameMetaInvalidError(AnalysisError):
    pass


class TransformOpUnsupportedError(AnalysisError):
    """Raised when an op is unknown or invalid for the input frame family."""


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
    def _template_fields(self) -> dict[str, str]:
        limit = self.details.get("limit")
        if isinstance(limit, int):
            return {
                "location": "frame.preview(limit=...)",
                "cause": "preview limit must be between 1 and 100.",
                "fix_snippet": "frame.preview(limit=10)",
                "doc": "marivo/skills/marivo-analysis/references/cheatsheet.md",
            }
        return {
            "location": "frame preview/read method",
            "cause": "frame read arguments are invalid.",
            "doc": "marivo/skills/marivo-analysis/references/cheatsheet.md",
        }


class FrameRefNotFound(AnalysisError): ...  # noqa: N818


class JobNotFoundError(AnalysisError): ...


class FrameCacheCorruptedError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        ref = self.details.get("ref", "?")
        cause = self.details.get("cause", "unknown")
        return {
            "location": f"frame cache for ref '{ref}'",
            "cause": f"persisted frame data is unreadable: {cause}",
            "fix_snippet": f"# Delete the corrupted artifact directory to force re-computation:\n# rm -rf .marivo/analysis/sessions/*/frames/{ref}/",
        }


class BackendError(AnalysisError): ...


class NoBackendFactoryError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        if not (isinstance(datasource, str) and datasource):
            return {
                "location": "analysis runtime backend configuration",
                "cause": (
                    "session has no backend factory configured; data-materializing "
                    "analysis intents need a datasource, backends={...}, or backend_factory=..."
                ),
                "fix_snippet": (
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
                "doc": "marivo/skills/marivo-semantic/references/datasource.md",
            }
        return {
            "location": "analysis runtime datasource backend factory",
            "cause": (
                f"datasource={datasource!r} resolved to None "
                "or a non-ibis object; the analysis runtime needs a live ibis "
                "backend."
            ),
            "fix_snippet": (
                "import marivo.analysis as mv\n"
                "import marivo.datasource as md\n"
                "\n"
                'md.register(md.DuckDBSpec(name="tiny_orders", path=":memory:"))\n'
            ),
            "doc": "marivo/skills/marivo-semantic/references/datasource.md",
        }


DatasourceMissingError = _datasource_errors.DatasourceMissingError
DatasourceSecretStorePermissionsError = _datasource_errors.DatasourceSecretStorePermissionsError
DatasourceEnvVarMissingError = _datasource_errors.DatasourceEnvVarMissingError
DatasourceBackendTypeUnsupportedError = _datasource_errors.DatasourceBackendTypeUnsupportedError
DatasourceSchemaVersionError = _datasource_errors.DatasourceSchemaVersionError
DatasourceConnectionError = _datasource_errors.DatasourceConnectionError
DatasourcePreviewError = _datasource_errors.DatasourcePreviewError
DatasourceMetadataError = _datasource_errors.DatasourceMetadataError


class DuplicateSessionNameError(AnalysisError): ...


class NoActiveSessionError(AnalysisError): ...


class SessionStateError(AnalysisError): ...


class SessionTimezoneConflict(SessionStateError):  # noqa: N818
    def _template_fields(self) -> dict[str, str]:
        persisted = self.details.get("persisted_report_tz", "<persisted>")
        requested = self.details.get("requested_report_tz", "<requested>")
        return {
            "location": "mv.session.get_or_create(report_timezone=...)",
            "cause": (
                f"session already has persisted report timezone {persisted!r}, "
                f"but {requested!r} was requested."
            ),
            "fix_snippet": (
                "Use the persisted report timezone, create a new session, "
                "or delete and recreate this session to re-bucket under a new report timezone."
            ),
            "doc": "docs/superpowers/specs/2026-06-17-timezone-two-axis-design.md",
        }


class SemanticProjectNotReadyError(AnalysisError): ...


class DimensionFieldNotFoundError(SemanticKindMismatchError):
    def _template_fields(self) -> dict[str, str]:
        dim = self.details.get("dimension_id")
        datasets = self.details.get("searched_datasets")
        metric_shape = self.details.get("metric_shape")
        available = self.details.get("available_ids")
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
        if isinstance(available, list) and available:
            preview = ", ".join(str(item) for item in available[:10])
            suffix = f" Available dimensions: {preview}"
            if len(available) > 10:
                suffix += f" (+{len(available) - 10} more)"
            cause += suffix
        return {
            "location": "session.observe dimensions argument",
            "cause": cause,
            "fix_snippet": (
                "import marivo.semantic as ms\n"
                "catalog = ms.load()\n"
                "catalog.list(kind='dimension')  # confirm available dimensions per entity\n"
                'session.observe(catalog.get("sales.revenue"), '
                'dimensions=[catalog.get("<existing_dimension>").ref])'
            ),
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class AmbiguousDimensionError(SemanticKindMismatchError):
    def _template_fields(self) -> dict[str, str]:
        dim = self.details.get("dimension_id")
        candidates = self.details.get("candidates")
        dim_ref = dim if isinstance(dim, str) and dim else "<dimension>"
        candidate_list = (
            ", ".join(candidates) if isinstance(candidates, list) and candidates else "<candidates>"
        )
        return {
            "location": "session.observe dimensions argument",
            "cause": (
                f"dimension {dim_ref!r} matches multiple datasets ({candidate_list}); "
                "v1 requires unique dimension names across a metric's datasets."
            ),
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class DimensionAcrossDatasetsError(SemanticKindMismatchError):
    def _template_fields(self) -> dict[str, str]:
        mapping = self.details.get("dimensions_by_dataset")
        return {
            "location": "session.observe dimensions argument",
            "cause": (
                "all dimensions must resolve to the same dataset in v1; "
                f"got dimensions_by_dataset={mapping!r}."
            ),
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class AxisNotInPanelDimensionsError(SemanticKindMismatchError):
    def _template_fields(self) -> dict[str, str]:
        axis = self.details.get("axis")
        available = self.details.get("available_dimensions")
        axis_ref = axis if isinstance(axis, str) and axis else "<axis>"
        available_list = (
            ", ".join(available) if isinstance(available, list) and available else "<dimensions>"
        )
        first_available = (
            available[0] if isinstance(available, list) and available else "<existing_dimension>"
        )
        return {
            "location": "session.decompose axis argument",
            "cause": (
                f"axis={axis_ref!r} is not in the panel frame dimensions "
                f"({available_list}); decompose requires axis to be one of the frame's "
                "segment dimensions."
            ),
            "fix_snippet": (
                f"# Choose the full catalog ref for panel dimension column {first_available!r}.\n"
                'axis = session.catalog.get("<domain.entity.dimension>").ref\n'
                "session.decompose(delta, axis=axis)"
            ),
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


class PanelGrainMismatchError(AlignmentFailedError):
    pass


class SegmentDimensionMismatchError(AlignmentFailedError):
    def _template_fields(self) -> dict[str, str]:
        current_dims = self.details.get("current_dimensions")
        baseline_dims = self.details.get("baseline_dimensions")
        if not isinstance(current_dims, list) or not isinstance(baseline_dims, list):
            return {}
        cur = ", ".join(current_dims)
        base = ", ".join(baseline_dims)
        extra_current = sorted(set(current_dims) - set(baseline_dims))
        extra_baseline = sorted(set(baseline_dims) - set(current_dims))
        cause = f"segment dimensions differ: current=[{cur}] vs baseline=[{base}]."
        if extra_current:
            cause += f" Extra in current: {', '.join(extra_current)}."
        if extra_baseline:
            cause += f" Extra in baseline: {', '.join(extra_baseline)}."
        return {
            "location": "session.compare call",
            "cause": cause,
            "fix_snippet": (
                "metric = session.catalog.get('model.metric')\n"
                'common_dim = session.catalog.get("model.entity.common_dim").ref\n'
                "current = session.observe(metric, dimensions=[common_dim])\n"
                "baseline = session.observe(metric, dimensions=[common_dim])\n"
                "delta = session.compare(current, baseline, "
                'alignment=mv.AlignmentPolicy(kind="window_bucket"))'
            ),
            "doc": "marivo/skills/marivo-analysis/references/pitfalls.md",
        }


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


class ComponentFrameUnavailableError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "frame.components()",
            "cause": (
                "Component frames are only available for derived ratio or "
                "weighted-average frames produced by component-aware observe/compare."
            ),
            "fix_snippet": (
                'frame = session.observe(session.catalog.get("model.derived_ratio"))\n'
                "components = frame.components()"
            ),
            "doc": "docs/superpowers/specs/2026-05-28-component-aware-frame-contract-design.md",
        }


class ComponentFrameMismatchError(AnalysisError):
    pass


class ComponentDecompositionError(AnalysisError):
    pass
