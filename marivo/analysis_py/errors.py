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
                "ms.list_metrics()  # confirm the exact id\n"
                'mv.observe(mv.MetricRef("<registered_metric_id>"), window="2026Q3")'
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
            "fix_snippet": 'mv.observe(mv.MetricRef("sales.revenue"), window="2026Q3")',
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
                'cur  = mv.observe(mv.MetricRef("sales.revenue"), window="2026Q3")\n'
                'base = mv.observe(mv.MetricRef("sales.revenue"), window="2025Q3")\n'
                "delta = mv.compare(cur, base)"
            ),
            "doc": "marivo-skill/marivo-py-analysis/references/pitfalls.md",
        }


class AlignmentFailedError(AnalysisError): ...


class MetricShapeUnsupportedError(AnalysisError):
    pass


class FrameMetaInvalidError(AnalysisError):
    pass


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
                '@ms.datasource(name="tiny_orders", backend_type="duckdb")\n'
                "def tiny_orders():\n"
                '    return ibis.duckdb.connect(":memory:")\n'
            ),
            "doc": "marivo-skill/marivo-py-semantic/references/pitfalls.md",
        }


class DuplicateSessionNameError(AnalysisError): ...


class NoActiveSessionError(AnalysisError): ...


class SessionStateError(AnalysisError): ...
