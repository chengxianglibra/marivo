"""SemanticCatalog — unified agent-facing read surface for marivo.semantic.

Public entrypoint: ms.load() -> SemanticCatalog"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, replace
from typing import (
    TYPE_CHECKING,
    Literal,
    Protocol,
    cast,
)

import ibis.expr.datatypes as dt
import ibis.expr.types as ir
from ibis.common.deferred import Deferred

from marivo._kernel.refs import (
    FieldKind,
    MeasureKind,
    Ref,
    SemanticKind,
    SemanticKindTag,
)
from marivo.datasource.errors import DatasourceError
from marivo.datasource.spi import (
    METRIC_PREVIEW_SAMPLE_SIZE,
    PREVIEW_DEFAULT_LIMIT,
    AuthoringScope,
    DatasourceConnectionService,
    PreviewExecutionLimits,
    PreviewResult,
    PreviewSamplePolicy,
    PreviewWarning,
    QueryParamScalar,
    QueryParamScalarList,
    backend_errors,
    preview_from_pandas,
    preview_ibis_table,
    require_profile_for_backend_type,
    timestamp_is_instant,
    validate_preview_limit,
)
from marivo.semantic.catalog_model.inputs import CatalogInputLookup
from marivo.semantic.catalog_model.refs import (
    _make_ref,
)
from marivo.semantic.dtos import PreviewBatchResult
from marivo.semantic.errors import (
    ErrorKind,
    SemanticRuntimeError,
    _raise,
)
from marivo.semantic.expressions.ir import CompiledExpressionSidecar
from marivo.semantic.ir import (
    DimensionIR,
    StrptimeParse,
    is_time_bearing_format,
)
from marivo.semantic.numeric_operations import numeric_failure
from marivo.semantic.runtime_check.numeric_preview import (
    lower_preview_metric,
    validate_preview_metric,
)
from marivo.semantic.runtime_check.preview_scope import (
    NormalizedPreviewScope,
    preflight_preview_scope,
)
from marivo.semantic.runtime_check.resolver import SemanticResolver
from marivo.semantic.static_check.source_timezone import (
    effective_source_timezone,
    source_timezone_axis,
)

if TYPE_CHECKING:
    from ibis.backends import BaseBackend

    from marivo.semantic.static_check.registry import Registry


@dataclass(frozen=True)
class _BatchPreviewItem:
    order: int
    ref: Ref[SemanticKindTag]
    kind: SemanticKind
    bindings: NormalizedPreviewScope


@contextmanager
def _preview_reader(
    bindings: NormalizedPreviewScope, connections: DatasourceConnectionService
) -> Iterator[BaseBackend]:
    """Apply native query limits before metadata or business-data resolution."""
    profile = require_profile_for_backend_type(bindings.backend)
    timeout = profile.native_query_timeout
    connection_scope = (
        connections.terminal_scope(bindings.timeout_seconds)
        if profile.dialect.preview_requires_terminal_scope
        else nullcontext()
    )
    with connection_scope:
        backend = connections.session_backend(bindings.datasource_id)
        with (
            backend_errors(backend),
            timeout(backend, bindings.timeout_seconds) if timeout is not None else nullcontext(),
        ):
            yield backend


def _preview_execution_limits(bindings: NormalizedPreviewScope) -> PreviewExecutionLimits:
    return PreviewExecutionLimits(
        backend=bindings.backend,
        timeout_seconds=bindings.timeout_seconds,
        timeout_enforced=(
            require_profile_for_backend_type(bindings.backend).native_query_timeout is not None
        ),
    )


def _preview_timezones_for_field(
    *,
    column_name: str,
    field_ir: DimensionIR,
    registry: Registry,
    physical_type: dt.DataType,
    engine: str,
) -> dict[str, dict[str, str | None]]:

    if not field_ir.is_time_dimension:
        return {}
    if timestamp_is_instant(physical_type, engine):
        return {column_name: {"kind": "instant"}}
    source = effective_source_timezone(
        registry, source_timezone_axis(registry, field_ir.semantic_id)
    )

    civil_date = physical_type.is_date() or (
        isinstance(field_ir.parse, StrptimeParse)
        and not is_time_bearing_format(field_ir.parse.format)
    )
    return {
        column_name: {
            "kind": "civil_date" if civil_date else "wall_clock",
            "timezone": source.timezone if source is not None else None,
            "origin": source.origin if source is not None else None,
            "inherited_from": source.inherited_from if source is not None else None,
        }
    }


def _metric_preview_warning() -> PreviewWarning:
    return PreviewWarning(
        kind="approximate_preview",
        message=(
            f"metric preview aggregates at most {METRIC_PREVIEW_SAMPLE_SIZE:,} Entity "
            "output rows per Entity, also bounded by scope.max_rows; unordered input "
            "is not a representative sample or an estimate of the full Metric"
        ),
    )


def _attach_preview_scope(
    result: PreviewResult,
    *,
    bindings: NormalizedPreviewScope,
    approximate_input: bool = False,
) -> PreviewResult:
    """Attach only current execution scope facts to one preview result."""
    coverage = replace(
        result.coverage,
        scopes=bindings.scopes,
        scope_exactness=("sample_only" if approximate_input else result.coverage.scope_exactness),
    )
    return replace(result, coverage=coverage)


class _PreviewProject(Protocol):
    @property
    def _expression_sidecar(self) -> CompiledExpressionSidecar | None: ...


class PreviewLookup(CatalogInputLookup, Protocol):
    @property
    def _project(self) -> _PreviewProject: ...

    def _require_ready(self) -> Registry: ...

    def _require_ref(self, ref: Ref[SemanticKindTag]) -> None: ...

    def _semantic_resolver(
        self,
        *,
        connections: DatasourceConnectionService,
        sample_size: int | None = None,
        entity_scopes: Mapping[str, AuthoringScope] | None = None,
        source_bindings: Mapping[str, Mapping[str, QueryParamScalar | QueryParamScalarList]]
        | None = None,
    ) -> SemanticResolver: ...


@dataclass(frozen=True, slots=True)
class _PreviewRequest:
    catalog: PreviewLookup
    ref: Ref[SemanticKindTag]
    bindings: NormalizedPreviewScope
    connections: DatasourceConnectionService
    preview_limit: int
    row_limit: int
    include_types: bool
    context_columns: tuple[str, ...] | None

    def _read(
        self,
        table: ir.Table,
        *,
        kind: Literal["semantic_dataset", "semantic_measure", "semantic_field"],
        limit: int,
        timezones: dict[str, dict[str, str | None]] | None = None,
    ) -> PreviewResult:
        return preview_ibis_table(
            table,
            kind=kind,
            ref=self.ref.path,
            limit=limit,
            sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=limit),
            include_types=self.include_types,
            timezones=timezones,
            report_tz=None,
            read_table=lambda expression, bound: self.connections.collect_source(
                self.bindings.datasource_id,
                expression,
                purpose="semantic.preview",
                max_rows=bound,
            ),
        )

    def execute(self) -> PreviewResult:
        kind = self.ref.kind
        ref_str = self.ref.path
        bindings = self.bindings
        reg = self.catalog._reg
        resolver = self.catalog._semantic_resolver(
            connections=self.connections,
            sample_size=(METRIC_PREVIEW_SAMPLE_SIZE if kind == SemanticKind.METRIC else None),
            entity_scopes=bindings.entity_scopes,
            source_bindings=bindings.source_bindings,
        )
        if kind is SemanticKind.METRIC:
            return self._metric(resolver)
        if kind == SemanticKind.ENTITY:
            table = resolver.entity(_make_ref(ref_str, SemanticKind.ENTITY))
            return self._read(
                table,
                kind="semantic_dataset",
                limit=self.row_limit,
            )
        if kind == SemanticKind.MEASURE:
            measure_ir = reg.measures[ref_str]
            parent_table = resolver.entity(_make_ref(measure_ir.entity, SemanticKind.ENTITY))
            measure_value = resolver.measure(_make_ref(ref_str, SemanticKind.MEASURE))
            measure_column_name = ref_str.rsplit(".", 1)[-1]
            preview_table = parent_table.select(measure_value.name(measure_column_name))
            return self._read(
                preview_table,
                kind="semantic_measure",
                limit=self.row_limit,
            )
        if kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
            return self._field(resolver)
        if kind is SemanticKind.RELATIONSHIP:
            return self._relationship(resolver)
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"catalog.preview() does not support {kind} refs.",
            cls=SemanticRuntimeError,
            refs=(ref_str,),
            details={"kind": str(kind)},
        )

    def _metric(self, resolver: SemanticResolver) -> PreviewResult:
        bindings = self.bindings
        ref_str = self.ref.path

        metric = self.catalog._resolved_value(self.ref)
        assert isinstance(metric, Deferred)
        expression, checks, samples = lower_preview_metric(metric, resolver, self.catalog._reg)
        frame = self.connections.collect_source(
            bindings.datasource_id, expression, purpose="semantic.preview", max_rows=1
        )
        for name, check in checks:
            if frame[name].fillna(False).any():
                numeric_failure(check.expected, f"{ref_str}: {check.received}")
        result = preview_from_pandas(
            frame.loc[:, ["value"]],
            kind="semantic_metric",
            ref=ref_str,
            requested_limit=self.preview_limit,
            sample_policy=PreviewSamplePolicy(
                method="pre_aggregate_limit", limit=METRIC_PREVIEW_SAMPLE_SIZE
            ),
            types={"value": str(expression.value.type())} if self.include_types else {},
            warnings=(_metric_preview_warning(),),
        )
        entity_rows = tuple((entity_id, int(frame[name].iloc[0])) for entity_id, name in samples)
        entity_limits = tuple(
            (entity_id, min(scope.max_rows, METRIC_PREVIEW_SAMPLE_SIZE))
            for entity_id, scope in bindings.scopes
        )
        limits = dict(entity_limits)
        return replace(
            result,
            coverage=replace(
                result.coverage,
                entity_rows=entity_rows,
                entity_row_limits=entity_limits,
                scope_exhaustion=(
                    "exhaustive"
                    if all(count < limits[entity_id] for entity_id, count in entity_rows)
                    else "unknown"
                ),
            ),
        )

    def _field(self, resolver: SemanticResolver) -> PreviewResult:
        kind = self.ref.kind
        ref_str = self.ref.path
        reg = self.catalog._reg
        bindings = self.bindings
        field_ir = reg.dimensions[ref_str]
        parent_table = resolver.entity(_make_ref(field_ir.entity, SemanticKind.ENTITY))
        field_value = resolver.dimension(cast("Ref[FieldKind]", _make_ref(ref_str, kind)))
        field_column_name = ref_str.rsplit(".", 1)[-1]
        selected_context = self.context_columns
        if selected_context is None:
            selected_context = tuple(
                column for column in parent_table.columns if column != field_column_name
            )[:3]
        missing_context = [
            column for column in selected_context if column not in parent_table.columns
        ]
        if missing_context:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                "Field preview context columns are not present on parent "
                f"dataset: {missing_context}",
                cls=SemanticRuntimeError,
                refs=(ref_str,),
            )
        preview_table = parent_table.select(
            *[parent_table[column] for column in selected_context],
            field_value.name(field_column_name),
        )
        result = self._read(
            preview_table,
            kind="semantic_field",
            limit=self.row_limit,
            timezones=_preview_timezones_for_field(
                column_name=field_column_name,
                field_ir=field_ir,
                registry=reg,
                physical_type=field_value.type(),
                engine=bindings.backend,
            ),
        )
        return result

    def _relationship(self, resolver: SemanticResolver) -> PreviewResult:
        reg = self.catalog._reg
        ref_str = self.ref.path
        relationship = reg.relationships[ref_str]
        left = resolver.entity(_make_ref(relationship.from_entity, SemanticKind.ENTITY))
        right = resolver.entity(_make_ref(relationship.to_entity, SemanticKind.ENTITY))
        left_names: list[str] = []
        right_names: list[str] = []
        left_values = []
        right_values = []
        for index, key in enumerate(relationship.keys, start=1):
            from_key, to_key = key.from_key, key.to_key
            from_kind = (
                SemanticKind.TIME_DIMENSION
                if reg.dimensions[from_key].is_time_dimension
                else SemanticKind.DIMENSION
            )
            to_kind = (
                SemanticKind.TIME_DIMENSION
                if reg.dimensions[to_key].is_time_dimension
                else SemanticKind.DIMENSION
            )
            left_name = f"from_key_{index}"
            right_name = f"to_key_{index}"
            left_names.append(left_name)
            right_names.append(right_name)
            left_values.append(
                resolver.dimension_on(
                    cast("Ref[FieldKind]", _make_ref(from_key, from_kind)), left
                ).name(left_name)
            )
            right_values.append(
                resolver.dimension_on(
                    cast("Ref[FieldKind]", _make_ref(to_key, to_kind)), right
                ).name(right_name)
            )
        left_keys = left.select(*left_values)
        right_keys = right.select(*right_values)
        joined = left_keys.join(
            right_keys,
            predicates=[
                left_keys[left_name] == right_keys[right_name]
                for left_name, right_name in zip(left_names, right_names, strict=True)
            ],
            how="inner",
        ).select(*(left_names + right_names))
        return self._read(
            joined,
            kind="semantic_dataset",
            limit=self.preview_limit,
        )


@dataclass(frozen=True, slots=True)
class CatalogPreviewer:
    """Execute bounded reads with the Catalog's captured scope and connections."""

    catalog: PreviewLookup

    def one(
        self,
        ref: Ref[SemanticKindTag],
        *,
        bindings: NormalizedPreviewScope,
        connections: DatasourceConnectionService,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
        context_columns: Iterable[str] | None = None,
        preflighted: bool = False,
    ) -> PreviewResult:
        """Execute the existing one-object preview contract."""
        reg = self.catalog._require_ready()
        ref_obj = ref
        self.catalog._require_ref(ref_obj)
        ref_str = ref_obj.path
        kind = ref_obj.kind
        sidecar = self.catalog._project._expression_sidecar
        if sidecar is None:
            _raise(
                ErrorKind.PROJECT_NOT_LOADED,
                "Semantic catalog expression sidecar is unavailable. Construct a fresh catalog with ms.load().",
                cls=SemanticRuntimeError,
                refs=(ref_str,),
            )
        if kind is SemanticKind.METRIC:
            metric = self.catalog._resolved_value(ref_obj)
            assert isinstance(metric, Deferred)
            validate_preview_metric(metric)
        preview_limit = validate_preview_limit(limit)
        row_preview_limit = min(
            preview_limit,
            *(bound_scope.max_rows for _entity_id, bound_scope in bindings.scopes),
        )
        is_field_preview = kind in {
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
        }
        if context_columns is not None and not is_field_preview:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                "catalog.preview(..., context_columns=...) is only valid for dimension refs.",
                cls=SemanticRuntimeError,
                refs=(ref_str,),
                details={"query_executed": False},
            )
        selected_context_input = tuple(context_columns) if context_columns is not None else None

        request = _PreviewRequest(
            self.catalog,
            ref_obj,
            bindings,
            connections,
            preview_limit,
            row_preview_limit,
            include_types,
            selected_context_input,
        )

        with _preview_reader(bindings, connections):
            if not preflighted:
                preflight_preview_scope(
                    bindings,
                    registry=reg,
                    connections=connections,
                    project_root=self.catalog.workspace_dir,
                )
            result = replace(
                request.execute(), execution_limits=_preview_execution_limits(bindings)
            )
        return _attach_preview_scope(
            result,
            bindings=bindings,
            approximate_input=kind is SemanticKind.METRIC,
        )

    def batch(
        self,
        refs: Sequence[Ref[SemanticKindTag]],
        *,
        normalized: tuple[NormalizedPreviewScope, ...],
        connections: DatasourceConnectionService,
        stack: ExitStack,
        limit: int,
        include_types: bool,
    ) -> PreviewBatchResult:
        reg = self.catalog._require_ready()
        ref_objects = tuple(refs)
        resolved = tuple((ref.path, ref.kind) for ref in ref_objects)
        preview_limit = validate_preview_limit(limit)
        sidecar = self.catalog._project._expression_sidecar
        assert sidecar is not None
        readers: dict[int, DatasourceConnectionService] = {}

        def reader_for(bindings: NormalizedPreviewScope) -> DatasourceConnectionService:
            timeout = bindings.timeout_seconds
            reader = readers.get(timeout)
            if reader is None:
                reader = stack.enter_context(
                    connections.operation(terminal_timeout_seconds=timeout)
                )
                readers[timeout] = reader
            return reader

        checked_scopes: set[tuple[tuple[str, AuthoringScope], ...]] = set()
        for bindings in normalized:
            if bindings.scopes in checked_scopes:
                continue
            reader = reader_for(bindings)
            with _preview_reader(bindings, reader):
                preflight_preview_scope(
                    bindings,
                    registry=reg,
                    connections=reader,
                    project_root=self.catalog.workspace_dir,
                )
            checked_scopes.add(bindings.scopes)
        items = tuple(
            _BatchPreviewItem(order, ref_obj, kind, bindings)
            for order, (ref_obj, (_ref_id, kind), bindings) in enumerate(
                zip(ref_objects, resolved, normalized, strict=True)
            )
        )

        groups: dict[tuple[object, ...], list[_BatchPreviewItem]] = {}
        row_kinds = {
            SemanticKind.ENTITY,
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
            SemanticKind.MEASURE,
        }
        for item in items:
            identity = (
                item.bindings.datasource_id,
                item.bindings.entity_ids,
                item.bindings.scopes,
                item.bindings.timeout_seconds,
            )
            key = ("row", *identity) if item.kind in row_kinds else ("relationship", item.order)
            groups.setdefault(key, []).append(item)

        by_order: dict[int, PreviewResult] = {}
        for group_key, group_items in groups.items():
            bindings = group_items[0].bindings
            reader = reader_for(bindings)
            try:
                if group_key[0] == "row":
                    with _preview_reader(bindings, reader):
                        raw_results = self._preview_row_group(
                            tuple(group_items),
                            connections=reader,
                            limit=preview_limit,
                            include_types=include_types,
                        )
                        raw_results = tuple(
                            replace(
                                result,
                                execution_limits=_preview_execution_limits(bindings),
                            )
                            for result in raw_results
                        )
                    results = tuple(
                        _attach_preview_scope(result, bindings=item.bindings)
                        for item, result in zip(group_items, raw_results, strict=True)
                    )
                else:
                    item = group_items[0]
                    results = (
                        self.one(
                            item.ref,
                            connections=reader,
                            bindings=item.bindings,
                            limit=preview_limit,
                            include_types=include_types,
                            preflighted=True,
                        ),
                    )
            except (SemanticRuntimeError, DatasourceError):
                raise
            except Exception as exc:
                _raise(
                    ErrorKind.MATERIALIZE_FAILED,
                    f"Batch preview {group_key[0]} group failed: {exc}",
                    cls=SemanticRuntimeError,
                    refs=tuple(item.ref.path for item in group_items),
                    details={"group": str(group_key[0])},
                )
            by_order.update(
                (item.order, result) for item, result in zip(group_items, results, strict=True)
            )
        return PreviewBatchResult(results=tuple(by_order[index] for index in range(len(items))))

    def _preview_row_group(
        self,
        items: tuple[_BatchPreviewItem, ...],
        *,
        connections: DatasourceConnectionService,
        limit: int,
        include_types: bool,
    ) -> tuple[PreviewResult, ...]:
        reg = self.catalog._require_ready()
        bindings = items[0].bindings
        entity_id = bindings.entity_ids[0]
        row_limit = min(limit, bindings.scopes[0][1].max_rows)
        resolver = self.catalog._semantic_resolver(
            connections=connections,
            entity_scopes=bindings.entity_scopes,
            source_bindings=bindings.source_bindings,
        )
        parent_table = resolver.entity(_make_ref(entity_id, SemanticKind.ENTITY))
        raw_columns = tuple(parent_table.columns)
        include_entity = any(item.kind == SemanticKind.ENTITY for item in items)
        raw_selected = set(raw_columns if include_entity else ())
        aliases: dict[int, str] = {}
        contexts: dict[int, tuple[str, ...]] = {}
        semantic_values = []
        used_names = set(raw_columns)
        for item in items:
            if item.kind == SemanticKind.ENTITY:
                continue
            alias = f"__marivo_preview_{item.order}"
            while alias in used_names:
                alias = f"_{alias}"
            used_names.add(alias)
            aliases[item.order] = alias
            if item.kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
                field_ir = reg.dimensions[item.ref.path]
                field_name = item.ref.name
                context = tuple(column for column in raw_columns if column != field_name)[:3]
                contexts[item.order] = context
                raw_selected.update(context)
                value = resolver.dimension(cast("Ref[FieldKind]", item.ref))
            else:
                value = resolver.measure(cast("Ref[MeasureKind]", item.ref))
            semantic_values.append(value.name(alias))

        selected_raw_columns = tuple(column for column in raw_columns if column in raw_selected)
        preview_table = parent_table.select(
            *[parent_table[column] for column in selected_raw_columns],
            *semantic_values,
        )
        dataframe = connections.collect_source(
            bindings.datasource_id,
            preview_table,
            purpose="semantic.preview_batch",
            max_rows=row_limit + 1,
        )
        schema_types = {name: str(dtype) for name, dtype in preview_table.schema().items()}

        report_tz = None
        results: list[PreviewResult] = []
        for item in items:
            if item.kind == SemanticKind.ENTITY:
                columns = raw_columns
                frame = dataframe.loc[:, list(columns)]
                result_types = (
                    {column: schema_types[column] for column in columns} if include_types else {}
                )
                kind: Literal["semantic_dataset", "semantic_field", "semantic_measure"] = (
                    "semantic_dataset"
                )
                timezones: dict[str, dict[str, str | None]] = {}
            else:
                alias = aliases[item.order]
                semantic_name = item.ref.name
                columns = (*contexts.get(item.order, ()), alias)
                frame = dataframe.loc[:, list(columns)].rename(columns={alias: semantic_name})
                result_types = (
                    {
                        **{column: schema_types[column] for column in columns if column != alias},
                        semantic_name: schema_types[alias],
                    }
                    if include_types
                    else {}
                )
                kind = (
                    "semantic_field"
                    if item.kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}
                    else "semantic_measure"
                )
                timezones = {}
                if item.kind == SemanticKind.TIME_DIMENSION:
                    field_ir = reg.dimensions[item.ref.path]
                    timezones = _preview_timezones_for_field(
                        column_name=semantic_name,
                        field_ir=field_ir,
                        registry=reg,
                        physical_type=preview_table.schema()[alias],
                        engine=bindings.backend,
                    )
            result = preview_from_pandas(
                frame,
                kind=kind,
                ref=item.ref.path,
                requested_limit=row_limit,
                sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=row_limit),
                types=result_types,
                timezones=timezones,
                report_tz=report_tz,
            )
            results.append(result)
        return tuple(results)
