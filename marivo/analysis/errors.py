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
        self.hint = hint
        self.details = details or {}

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
                "project = ms.find_project()\n"
                "project.load()\n"
                "project.list_metrics()  # confirm the exact id\n"
                'session.observe(mv.MetricRef("<registered_metric_id>"), '
                'timescope={"start": "2026-07-01", "end": "2026-09-30"})'
            ),
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
        }


class WindowInvalidError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        window = self.details.get("window") or self.details.get("timescope")
        window_ref = window if isinstance(window, str) and window else "<timescope>"
        return {
            "location": "session.observe timescope or frame window argument",
            "cause": f"timescope={window_ref} could not be parsed.",
            "fix_snippet": (
                'session.observe(mv.MetricRef("sales.revenue"), '
                'timescope={"start": "2026-07-01", "end": "2026-09-30"})'
            ),
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
                "location": "session.discover(driver_axes) arguments",
                "cause": (
                    "discover(objective='driver_axes') requires a non-empty "
                    "search_space=[DimensionRef(...), ...]."
                ),
                "fix_snippet": (
                    'session.discover(delta, objective="driver_axes",\n'
                    '            search_space=[mv.DimensionRef("country")])'
                ),
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
            }
        if isinstance(objective, str) and isinstance(source_kind_value, str):
            return {
                "location": "session.discover dispatch",
                "cause": (
                    f"discover objective {objective!r} does not accept source kind "
                    f"{source_kind_value!r}; allowed source kinds: {expected_kind_str}."
                ),
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
            }
        if expected_kind == "candidate_set":
            return {
                "location": "CandidateSet.select call",
                "cause": (
                    f"got kind {got_kind}, expected {expected_kind}; CandidateSet.select only "
                    "operates on CandidateSet artifacts."
                ),
                "fix_snippet": (
                    'cands = session.discover(metric, objective="point_anomalies")\n'
                    'window = cands.select(rank=1, attribute="window")'
                ),
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
            }
        if expected_kind == "MetricRef":
            return {
                "location": "session.observe call",
                "cause": (
                    f"got {got_kind}, expected {expected_kind}; observe requires "
                    "metric=mv.MetricRef(...)."
                ),
                "fix_snippet": (
                    'session.observe(mv.MetricRef("sales.revenue"), '
                    'timescope={"start": "2026-07-01", "end": "2026-09-30"})'
                ),
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
            }
        if got_kind != "delta_frame" or expected_kind != "metric_frame":
            return {
                "cause": (
                    f"got kind {got_kind}, expected {expected_kind}; input frame kind does not "
                    "match the requested analysis operation."
                ),
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
            }
        return {
            "location": "session.compare call",
            "cause": (
                f"got kind {got_kind}, expected {expected_kind}; this usually means passing a "
                "compare result where an observe result is required."
            ),
            "fix_snippet": (
                'cur  = session.observe(mv.MetricRef("sales.revenue"), '
                'timescope={"start": "2026-07-01", "end": "2026-09-30"})\n'
                'base = session.observe(mv.MetricRef("sales.revenue"), '
                'timescope={"start": "2025-07-01", "end": "2025-09-30"})\n'
                'delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))'
            ),
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
            }
        if case == "legacy_calendar_bucket":
            return {
                "location": "mv.AlignmentPolicy(...)",
                "cause": (
                    "alignment kind 'calendar_bucket' was renamed; use 'window_bucket' "
                    "for request-window bucket spine alignment."
                ),
                "fix_snippet": 'mv.AlignmentPolicy(kind="window_bucket")',
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
            }
        if case == "unexpected_calendar":
            return {
                "location": "mv.AlignmentPolicy(...)",
                "cause": (
                    "window_bucket alignment infers buckets from the input windows and does not "
                    "accept a calendar argument."
                ),
                "fix_snippet": 'mv.AlignmentPolicy(kind="window_bucket")  # no calendar argument',
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
            }
        return {
            "location": "mv.AlignmentPolicy(...)",
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
        }


class LagPolicyValidationError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        case = self.details.get("case")
        if case == "unsupported_mode":
            mode = self.details.get("mode")
            mode_str = mode if isinstance(mode, str) and mode else "<mode>"
            return {
                "location": "mv.LagPolicy(...)",
                "cause": f"lag mode {mode_str!r} is not supported; only mode='single' is implemented in v1.",
                "fix_snippet": 'mv.LagPolicy(mode="single", offset=0)',
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
            }
        if case == "nonzero_offset":
            offset = self.details.get("offset")
            offset_str = str(offset) if isinstance(offset, int) else "<offset>"
            return {
                "location": "mv.LagPolicy(...)",
                "cause": (
                    f"offset={offset_str} is not supported in v1; only zero-lag correlation is implemented."
                ),
                "fix_snippet": 'mv.LagPolicy(mode="single", offset=0)',
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
            }
        return {
            "location": "mv.LagPolicy(...)",
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
                    '    metric=mv.MetricRef("sales.revenue"),\n'
                    '    semantic_kind="segmented",\n'
                    '    measure_column="value",\n'
                    '    axes={"country": mv.DimensionRef("country")},\n'
                    '    semantic_model="sales",\n'
                    ")"
                )
                if missing_fields & {"metric", "measure_column", "semantic_model"}
                else "Pass the missing typed ref or column name shown in error details.",
                "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
            }
        return {
            "location": f"session.promote_{target}",
            "cause": "promotion metadata is incomplete or ambiguous.",
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
        }


class TestShapeNotTestableError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.hypothesis_test call",
            "cause": "mean_changed needs paired observations; scalar frames or too-small paired samples cannot be tested in v1.",
            "fix_snippet": (
                'cur = session.observe(mv.MetricRef("sales.revenue"), timescope={"start": "2026-07-01", "end": "2026-07-31"}, grain="day")\n'
                'base = session.observe(mv.MetricRef("sales.revenue"), timescope={"start": "2025-07-01", "end": "2025-07-31"}, grain="day")\n'
                "session.hypothesis_test(cur, base)"
            ),
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
        }


class TestPolicyError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.hypothesis_test policy arguments",
            "cause": "test v1 only supports mean_changed, window_bucket alignment, and shape-compatible SamplingPolicy.pairing.",
            "fix_snippet": "session.hypothesis_test(cur, base, sampling=mv.SamplingPolicy(pairing='window_bucket'), alpha=0.05)",
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
        }


class TestAlignmentError(AlignmentFailedError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.hypothesis_test alignment",
            "cause": "the input frames did not produce any paired samples after alignment and null dropping.",
            "fix_snippet": "session.hypothesis_test(cur, base, alignment=mv.AlignmentPolicy(kind='window_bucket'))",
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
        }


class ForecastShapeUnsupportedError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.forecast input frame",
            "cause": "forecast v1 accepts only MetricFrame time_series or panel shapes.",
            "fix_snippet": 'history = session.observe(mv.MetricRef("sales.revenue"), timescope={"start": "2026-01-01", "end": "2026-03-31"}, grain="day")\nsession.forecast(history, horizon=30)',
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
        }


class ForecastPolicyError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.forecast policy arguments",
            "cause": "horizon, interval_level, model, seasonality_period, or grain is outside the v1 supported contract.",
            "fix_snippet": "session.forecast(history, horizon=30, model='seasonal_naive', seasonality_period=7, interval_level=0.95)",
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
        }


class ForecastInsufficientHistoryError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.forecast history",
            "cause": "the time_series input has fewer training points than the selected model requires.",
            "fix_snippet": 'history = session.observe(mv.MetricRef("sales.revenue"), timescope={"start": "2026-01-01", "end": "2026-03-31"}, grain="day")',
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
        }


class ForecastInputQualityError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.forecast history data",
            "cause": "forecast does not silently impute NaN values or fill missing time buckets.",
            "fix_snippet": "clean = session.transform(history, op='window', order_by='time')  # or impute upstream before forecasting",
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
        }


class QualityShapeUnsupportedError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        return {
            "location": "session.assess_quality target",
            "cause": "assess_quality v1 only supports MetricFrame targets; other frame families are planned for v1.1+.",
            "fix_snippet": "report = session.assess_quality(metric_frame)",
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
                "doc": "marivo-skills/marivo-analysis/references/cheatsheet.md",
            }
        return {
            "location": "frame preview/read method",
            "cause": "frame read arguments are invalid.",
            "doc": "marivo-skills/marivo-analysis/references/cheatsheet.md",
        }


class FrameRefNotFound(AnalysisError): ...  # noqa: N818


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
                    'mv.datasources.register(md.DatasourceSpec(name="tiny_orders", backend_type="duckdb", path=":memory:"))\n'
                    'session = mv.session.get_or_create(name="analysis")  # auto-loads from datasource\n'
                    "\n"
                    "# Or pass an explicit factory (no datasource lookup):\n"
                    "import ibis\n"
                    "session = mv.session.attach("
                    'name="analysis", '
                    'backend_factory=lambda name: ibis.duckdb.connect(":memory:"), '
                    "use_datasources=False)"
                ),
                "doc": "marivo-skills/marivo-semantic/references/datasource.md",
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
                'mv.datasources.register(md.DatasourceSpec(name="tiny_orders", backend_type="duckdb", path=":memory:"))\n'
            ),
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourceMissingError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        available = self.details.get("available")
        backend_type = self.details.get("backend_type")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        bt_arg = (
            f'backend_type="{backend_type}", '
            if isinstance(backend_type, str) and backend_type
            else 'backend_type="<backend_type>", '
        )
        available_line = (
            f"datasource not found; configured datasources: {available}."
            if isinstance(available, list) and available
            else "datasource not found; no datasources are configured yet."
        )
        return {
            "location": ".marivo/datasource",
            "cause": f"datasource {ds_ref!r} is not configured; {available_line}",
            "fix_snippet": (
                "import marivo.analysis as mv\n"
                "import marivo.datasource as md\n"
                "mv.datasources.register(\n"
                f'    md.DatasourceSpec(name="{ds_ref}", {bt_arg}host="...", port=..., user_env="USER_VAR")\n'
                ")\n"
                "# Sensitive fields go via *_env on DatasourceSpec."
            ),
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourceSecretStorePermissionsError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        path = self.details.get("path")
        mode = self.details.get("mode")
        path_ref = path if isinstance(path, str) and path else "~/.marivo/secrets.toml"
        mode_ref = oct(mode) if isinstance(mode, int) else "unknown"
        return {
            "location": path_ref,
            "cause": (
                f"datasource secret store permissions are {mode_ref}; "
                "the file must be readable and writable only by the current user."
            ),
            "fix_snippet": "chmod 600 ~/.marivo/secrets.toml",
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourceEnvVarMissingError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        env_var = self.details.get("env_var")
        field_name = self.details.get("field")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "unknown_datasource"
        var_ref = env_var if isinstance(env_var, str) and env_var else "UNKNOWN_SECRET_ENV"
        field_ref = field_name if isinstance(field_name, str) and field_name else "secret_field"
        return {
            "location": f".marivo/datasource entry {ds_ref!r} field {field_ref!r}",
            "cause": (
                f"datasource field {field_ref!r} resolves to env var {var_ref!r}, "
                "but that variable is not set in os.environ and is not present in "
                "~/.marivo/secrets.toml."
            ),
            "fix_snippet": (
                f'export {var_ref}="secret_value"\n'
                f"import marivo.analysis as mv\n"
                f'mv.datasources.test("{ds_ref}")  # remembers the secret after validation'
            ),
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourceBackendTypeUnsupportedError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        backend_type = self.details.get("backend_type")
        supported = self.details.get("supported")
        bt_ref = (
            backend_type if isinstance(backend_type, str) and backend_type else "<backend_type>"
        )
        supported_line = (
            f"supported: {sorted(supported)}."
            if isinstance(supported, list | set | tuple) and supported
            else "no supported backend_type values registered."
        )
        return {
            "location": "mv.datasources backend dispatch",
            "cause": f"backend_type={bt_ref!r} is not handled by datasource backend dispatch; {supported_line}",
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourceSchemaVersionError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        got = self.details.get("got")
        expected = self.details.get("expected")
        path = self.details.get("path")
        got_ref = str(got) if got is not None else "<missing>"
        expected_ref = str(expected) if expected is not None else "<expected>"
        path_ref = path if isinstance(path, str) and path else ".marivo/datasource"
        return {
            "location": path_ref,
            "cause": (
                f"datasource registry schema_version={got_ref} is not supported by this "
                f"version of marivo.analysis (expected {expected_ref})."
            ),
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourceConnectionError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        cause = self.details.get("cause")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        cause_ref = (
            cause if isinstance(cause, str) and cause else "backend rejected the connection."
        )
        return {
            "location": f"mv.datasources.test({ds_ref!r}) dial",
            "cause": cause_ref,
            "fix_snippet": (
                "# verify host/port reachability and that env_ref secrets are exported, then:\n"
                "import marivo.analysis as mv\n"
                f"mv.datasources.test({ds_ref!r})"
            ),
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourcePreviewError(AnalysisError):
    pass


class DatasourceMetadataError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        table = self.details.get("table")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        table_ref = table if isinstance(table, str) and table else "<table>"
        return {
            "location": f"mv.datasources.inspect_table({ds_ref!r}, table={table_ref!r})",
            "cause": self.details.get("cause", "table metadata inspection failed"),
            "fix_snippet": (
                "import marivo.analysis as mv\n"
                f"mv.datasources.describe({ds_ref!r})\n"
                f"mv.datasources.test({ds_ref!r})\n"
                f"mv.datasources.inspect_table({ds_ref!r}, table={table_ref!r})"
            ),
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DuplicateSessionNameError(AnalysisError): ...


class NoActiveSessionError(AnalysisError): ...


class SessionStateError(AnalysisError): ...


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
                f"DimensionRef({dim_ref!r}) was not found on the derived metric's "
                f"component datasets or reachable relationship graph ({dataset_list})."
            )
        else:
            cause = (
                f"DimensionRef({dim_ref!r}) is not a field on any of the metric's "
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
                "project = ms.find_project()\n"
                "project.load()\n"
                "project.list_fields()  # confirm available fields per dataset\n"
                'session.observe(mv.MetricRef("sales.revenue"), '
                'dimensions=[mv.DimensionRef("<existing_field>")])'
            ),
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
                f"DimensionRef({dim_ref!r}) matches multiple datasets ({candidate_list}); "
                "v1 requires unique dimension names across a metric's datasets."
            ),
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
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
            "fix_snippet": (f'session.decompose(delta, axis=mv.DimensionRef("{first_available}"))'),
            "doc": "marivo-skills/marivo-analysis/references/pitfalls.md",
        }


class PanelGrainMismatchError(AlignmentFailedError):
    pass


class SegmentDimensionMismatchError(AlignmentFailedError):
    pass


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
                'frame = session.observe(mv.MetricRef("model.derived_ratio"))\n'
                "components = frame.components()"
            ),
            "doc": "docs/superpowers/specs/2026-05-28-component-aware-frame-contract-design.md",
        }


class ComponentFrameMismatchError(AnalysisError):
    pass


class ComponentDecompositionError(AnalysisError):
    pass
