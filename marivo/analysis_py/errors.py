"""Structured error hierarchy for marivo.analysis_py."""

from __future__ import annotations

from typing import Any


class AnalysisError(Exception):
    """Base class for all analysis_py errors."""

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
            context_lines.append(f"发生位置: {location}")
        if cause := self._resolved_field("cause"):
            context_lines.append(f"原因: {cause}")
        if self.hint:
            context_lines.append(f"建议: {self.hint}")
        if context_lines:
            lines.append("")
            lines.extend(context_lines)

        if fix_snippet := self._resolved_field("fix_snippet"):
            lines.append("")
            lines.append("正确写法:")
            lines.extend(f"  {line}" for line in fix_snippet.splitlines())

        if doc := self._resolved_field("doc"):
            lines.append("")
            lines.append(f"相关文档: {doc}")

        return "\n".join(lines)


class MetricNotFoundError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        metric_id = self.details.get("metric_id")
        model = self.details.get("model")
        metric = self.details.get("metric")
        metric_ref = None
        if isinstance(metric_id, str) and metric_id:
            metric_ref = metric_id
        elif isinstance(model, str) and model and isinstance(metric, str) and metric:
            metric_ref = f"{model}.{metric}"
        if not metric_ref:
            return {"location": "mv.observe call"}
        return {
            "location": "mv.observe call",
            "cause": f"metric_id={metric_ref} is not registered in the active semantic model.",
            "fix_snippet": (
                "import marivo.semantic_py as ms\n"
                "project = ms.find_project()\n"
                "project.load()\n"
                "project.list_metrics()  # confirm the exact id\n"
                'mv.observe(mv.MetricRef("<registered_metric_id>"), '
                'window={"start": "2026-07-01", "end": "2026-09-30"})'
            ),
            "doc": "marivo-skill/marivo-py-analysis/references/pitfalls.md",
        }


class WindowInvalidError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        window = self.details.get("window")
        window_ref = window if isinstance(window, str) and window else "<window>"
        return {
            "location": "mv.observe / mv.compare window argument",
            "cause": f"window={window_ref} could not be parsed by the active calendar.",
            "fix_snippet": (
                'mv.observe(mv.MetricRef("sales.revenue"), '
                'window={"start": "2026-07-01", "end": "2026-09-30"})'
            ),
            "doc": "marivo-skill/marivo-py-analysis/references/pitfalls.md",
        }


class WindowRelativeParseError(AnalysisError):
    pass


class TimezoneInvalidError(AnalysisError):
    pass


class WindowAmbiguousError(AnalysisError): ...


class SliceInvalidError(AnalysisError): ...


class SliceAmbiguousError(AnalysisError): ...


class SemanticKindMismatchError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
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
                "doc": "marivo-skill/marivo-py-analysis/references/pitfalls.md",
            }
        if expected_kind == "MetricRef":
            return {
                "location": "mv.observe call",
                "cause": (
                    f"got {got_kind}, expected {expected_kind}; observe requires "
                    "metric=mv.MetricRef(...)."
                ),
                "fix_snippet": (
                    'mv.observe(mv.MetricRef("sales.revenue"), '
                    'window={"start": "2026-07-01", "end": "2026-09-30"})'
                ),
                "doc": "marivo-skill/marivo-py-analysis/references/pitfalls.md",
            }
        if got_kind != "delta_frame" or expected_kind != "metric_frame":
            return {
                "cause": (
                    f"got kind {got_kind}, expected {expected_kind}; input frame kind does not "
                    "match the requested analysis operation."
                ),
                "doc": "marivo-skill/marivo-py-analysis/references/pitfalls.md",
            }
        return {
            "location": "mv.compare call",
            "cause": (
                f"got kind {got_kind}, expected {expected_kind}; this usually means passing a "
                "compare result where an observe result is required."
            ),
            "fix_snippet": (
                'cur  = mv.observe(mv.MetricRef("sales.revenue"), '
                'window={"start": "2026-07-01", "end": "2026-09-30"})\n'
                'base = mv.observe(mv.MetricRef("sales.revenue"), '
                'window={"start": "2025-07-01", "end": "2025-09-30"})\n'
                'delta = mv.compare(cur, base, alignment=mv.AlignmentPolicy(kind="calendar_bucket"))'
            ),
            "doc": "marivo-skill/marivo-py-analysis/references/pitfalls.md",
        }


class AlignmentFailedError(AnalysisError): ...


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
                    "analysis intents need backends={...} or backend_factory=..."
                ),
                "fix_snippet": (
                    "import ibis\n"
                    "import marivo.analysis_py as mv\n"
                    "\n"
                    'orders_backend = ibis.duckdb.connect(":memory:")\n'
                    'session = mv.attach(name="analysis", backends={"tiny_orders": orders_backend})\n'
                    "# or\n"
                    'session = mv.attach(name="analysis", backend_factory=lambda name: orders_backend)'
                ),
                "doc": "marivo-skill/marivo-py-analysis/references/pitfalls.md",
            }
        return {
            "location": "@ms.datasource backend factory",
            "cause": (
                f"datasource={datasource!r} returned None "
                "or a non-ibis object; the analysis runtime needs a live ibis "
                "backend."
            ),
            "fix_snippet": (
                "import ibis\n"
                "import marivo.semantic_py as ms\n"
                "\n"
                'wh = ms.datasource(name="tiny_orders", backend_type="duckdb")\n'
                "def tiny_orders():\n"
                '    return ibis.duckdb.connect(":memory:")\n'
            ),
            "doc": "marivo-skill/marivo-py-semantic/references/pitfalls.md",
        }


class DuplicateSessionNameError(AnalysisError): ...


class NoActiveSessionError(AnalysisError): ...


class SessionStateError(AnalysisError): ...


class SemanticProjectNotReadyError(AnalysisError): ...


class DimensionFieldNotFoundError(SemanticKindMismatchError):
    def _template_fields(self) -> dict[str, str]:
        dim = self.details.get("dimension_id")
        datasets = self.details.get("searched_datasets")
        dim_ref = dim if isinstance(dim, str) and dim else "<dimension>"
        dataset_list = (
            ", ".join(datasets) if isinstance(datasets, list) and datasets else "<datasets>"
        )
        return {
            "location": "mv.observe dimensions argument",
            "cause": (
                f"DimensionRef({dim_ref!r}) is not a field on any of the metric's "
                f"datasets ({dataset_list})."
            ),
            "fix_snippet": (
                "import marivo.semantic_py as ms\n"
                "project = ms.find_project()\n"
                "project.load()\n"
                "project.list_fields()  # confirm available fields per dataset\n"
                'mv.observe(mv.MetricRef("sales.revenue"), '
                'dimensions=[mv.DimensionRef("<existing_field>")])'
            ),
            "doc": "marivo-skill/marivo-py-analysis/references/pitfalls.md",
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
            "location": "mv.observe dimensions argument",
            "cause": (
                f"DimensionRef({dim_ref!r}) matches multiple datasets ({candidate_list}); "
                "v1 requires unique dimension names across a metric's datasets."
            ),
            "doc": "marivo-skill/marivo-py-analysis/references/pitfalls.md",
        }


class DimensionAcrossDatasetsError(SemanticKindMismatchError):
    def _template_fields(self) -> dict[str, str]:
        mapping = self.details.get("dimensions_by_dataset")
        return {
            "location": "mv.observe dimensions argument",
            "cause": (
                "all dimensions must resolve to the same dataset in v1; "
                f"got dimensions_by_dataset={mapping!r}."
            ),
            "doc": "marivo-skill/marivo-py-analysis/references/pitfalls.md",
        }


class AxisNotInPanelDimensionsError(SemanticKindMismatchError):
    def _template_fields(self) -> dict[str, str]:
        axis = self.details.get("axis")
        available = self.details.get("available_dimensions")
        axis_ref = axis if isinstance(axis, str) and axis else "<axis>"
        available_list = (
            ", ".join(available) if isinstance(available, list) and available else "<dimensions>"
        )
        return {
            "location": "mv.decompose axis argument",
            "cause": (
                f"axis={axis_ref!r} is not in the panel frame dimensions "
                f"({available_list}); decompose requires axis to be one of the frame's "
                "segment dimensions."
            ),
            "doc": "marivo-skill/marivo-py-analysis/references/pitfalls.md",
        }


class PanelGrainMismatchError(AlignmentFailedError):
    pass


class SegmentDimensionMismatchError(AlignmentFailedError):
    pass


class AlignmentPolicyNotApplicableError(AlignmentFailedError):
    pass
