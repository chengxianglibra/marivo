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
                    "analysis intents need a profile, backends={...}, or backend_factory=..."
                ),
                "fix_snippet": (
                    "import marivo.analysis_py as mv\n"
                    "\n"
                    "# Recommended: persist the connection once via the profile registry.\n"
                    'mv.profiles.set("tiny_orders", backend_type="duckdb", path=":memory:")\n'
                    'session = mv.session.create(name="analysis")  # auto-loads from profile\n'
                    "\n"
                    "# Or pass an explicit factory (no profile lookup):\n"
                    "import ibis\n"
                    "session = mv.session.attach("
                    'name="analysis", '
                    'backend_factory=lambda name: ibis.duckdb.connect(":memory:"), '
                    "use_profiles=False)"
                ),
                "doc": "marivo-skill/marivo-py-analysis/references/profiles.md",
            }
        return {
            "location": "@ms.datasource backend factory",
            "cause": (
                f"datasource={datasource!r} returned None "
                "or a non-ibis object; the analysis runtime needs a live ibis "
                "backend."
            ),
            "fix_snippet": (
                "import marivo.analysis_py as mv\n"
                "import marivo.semantic_py as ms\n"
                "\n"
                'ms.datasource(name="tiny_orders", backend_type="duckdb")\n'
                'mv.profiles.set("tiny_orders", backend_type="duckdb", path=":memory:")\n'
            ),
            "doc": "marivo-skill/marivo-py-analysis/references/profiles.md",
        }


class ProfileMissingError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        available = self.details.get("available")
        backend_type = self.details.get("backend_type")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        bt_arg = (
            f'backend_type="{backend_type}", '
            if isinstance(backend_type, str) and backend_type
            else ""
        )
        available_line = (
            f"profile not found; configured profiles: {available}."
            if isinstance(available, list) and available
            else "profile not found; no profiles are configured yet."
        )
        return {
            "location": "mv.profiles registry (~/.marivo/profiles/profiles.json)",
            "cause": f"datasource={ds_ref!r} {available_line}",
            "fix_snippet": (
                "import marivo.analysis_py as mv\n"
                f'mv.profiles.set({ds_ref!r}, {bt_arg}host="...", port=..., user="...")\n'
                f'# Sensitive fields go via *_env: mv.profiles.set({ds_ref!r}, ..., password_env="PWD_VAR")'
            ),
            "doc": "marivo-skill/marivo-py-analysis/references/profiles.md",
        }


class ProfileEnvVarMissingError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        env_var = self.details.get("env_var")
        field_name = self.details.get("field")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        var_ref = env_var if isinstance(env_var, str) and env_var else "<VAR_NAME>"
        field_ref = field_name if isinstance(field_name, str) and field_name else "<field>"
        return {
            "location": f"mv.profiles entry {ds_ref!r} field {field_ref!r}",
            "cause": (
                f"profile field {field_ref!r} resolves to env var {var_ref!r}, "
                "but that variable is not set in os.environ."
            ),
            "fix_snippet": f'export {var_ref}="<your secret>"',
            "doc": "marivo-skill/marivo-py-analysis/references/profiles.md",
        }


class ProfileSecretInPlaintextError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        field_name = self.details.get("field")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        field_ref = field_name if isinstance(field_name, str) and field_name else "<field>"
        env_ref = f"{field_ref}_env"
        return {
            "location": f"mv.profiles.set call for {ds_ref!r}",
            "cause": (
                f"field {field_ref!r} is a sensitive credential and must not be stored as a "
                "literal in the profile file."
            ),
            "fix_snippet": (
                "import marivo.analysis_py as mv\n"
                f'mv.profiles.set({ds_ref!r}, ..., {env_ref}="MY_SECRET_VAR")\n'
                f'# then: export MY_SECRET_VAR="<your secret>"'
            ),
            "doc": "marivo-skill/marivo-py-analysis/references/profiles.md",
        }


class ProfileFieldInvalidError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        field_name = self.details.get("field")
        reason = self.details.get("reason")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        field_ref = field_name if isinstance(field_name, str) and field_name else "<field>"
        reason_ref = reason if isinstance(reason, str) and reason else "invalid value"
        return {
            "location": f"mv.profiles entry {ds_ref!r} field {field_ref!r}",
            "cause": reason_ref,
            "doc": "marivo-skill/marivo-py-analysis/references/profiles.md",
        }


class ProfileBackendTypeUnsupportedError(AnalysisError):
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
            "location": "mv.profiles backend dispatch",
            "cause": f"backend_type={bt_ref!r} is not handled by the profile registry; {supported_line}",
            "doc": "marivo-skill/marivo-py-analysis/references/profiles.md",
        }


class ProfileSchemaVersionError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        got = self.details.get("got")
        expected = self.details.get("expected")
        path = self.details.get("path")
        got_ref = str(got) if got is not None else "<missing>"
        expected_ref = str(expected) if expected is not None else "<expected>"
        path_ref = path if isinstance(path, str) and path else "~/.marivo/profiles/profiles.json"
        return {
            "location": path_ref,
            "cause": (
                f"profile registry schema_version={got_ref} is not supported by this "
                f"version of marivo.analysis_py (expected {expected_ref})."
            ),
            "doc": "marivo-skill/marivo-py-analysis/references/profiles.md",
        }


class ProfileConnectionError(AnalysisError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        cause = self.details.get("cause")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        cause_ref = (
            cause if isinstance(cause, str) and cause else "backend rejected the connection."
        )
        return {
            "location": f"mv.profiles.test({ds_ref!r}) dial",
            "cause": cause_ref,
            "fix_snippet": (
                "# verify host/port reachability and that env_ref secrets are exported, then:\n"
                "import marivo.analysis_py as mv\n"
                f"mv.profiles.test({ds_ref!r})"
            ),
            "doc": "marivo-skill/marivo-py-analysis/references/profiles.md",
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
