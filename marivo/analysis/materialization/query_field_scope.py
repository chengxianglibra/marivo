"""Bind physical fields and source clock authorities in one contribution scope."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TypeAlias

import ibis.expr.types as ir

from marivo._definition.observations import ContributionBinding
from marivo._kernel.numeric_types import exact_unsigned
from marivo._kernel.refs import SemanticKind, ref
from marivo.analysis.binding.windows import validate_date_contribution
from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.source_time import (
    TimePredicateUse,
    entity_engine,
    source_time,
    source_time_predicate,
    temporal_bound,
)
from marivo.analysis.materialization.encoded_time import encoded_time
from marivo.analysis.model.time_authority import SourceTimeAuthority
from marivo.analysis.model.time_facts import FrameSourceTime, capture_source_times
from marivo.datasource.spi import localize, timestamp
from marivo.semantic.spi import (
    CompiledSemanticState,
    HourPrefixParse,
    Registry,
    StrptimeParse,
    TargetDimensionContract,
    effective_source_timezone,
    evaluate_expression_body,
    normalize_target_dimension,
    normalize_target_entity,
    numeric_failure,
    validate_bound_field,
)

TimeEncoding: TypeAlias = tuple[
    ir.Value,
    TargetDimensionContract,
    SourceTimeAuthority,
    ir.Value | None,
    TargetDimensionContract | None,
]


@dataclass(slots=True)
class QueryFieldScope:
    contribution: ContributionBinding
    state: CompiledSemanticState
    consumption_timezone: str
    reserved: set[str]
    dependencies: list[ir.Value] = field(default_factory=list)
    time_encodings: dict[str, TimeEncoding] = field(default_factory=dict)
    source_times: list[FrameSourceTime] = field(default_factory=list)

    @property
    def registry(self) -> Registry:
        return self.state.registry

    @property
    def zone(self) -> str:
        period = self.contribution.period
        return self.consumption_timezone if period is None else period.timezone

    def unique(self, name: str) -> str:
        while name in self.reserved:
            name += "_"
        self.reserved.add(name)
        return name

    def value(
        self,
        path: str,
        table: ir.Table,
        authority_zone: str | None = None,
        instant_read: bool = False,
    ) -> ir.Value:
        registry = self.registry
        state = self.state
        is_measure = path in registry.measures
        owner = registry.measures[path].entity if is_measure else registry.dimensions[path].entity
        target = (
            ref.measure(path)
            if is_measure
            else (
                ref.time_dimension(path)
                if registry.dimensions[path].is_time_dimension
                else ref.dimension(path)
            )
        )
        body = state.sidecar.bodies.get(target)
        if body is None:
            numeric_failure(f"a compiled physical field for {path}", "missing field body")
        result = evaluate_expression_body(
            catalog_definition_fingerprint=state.definition_fingerprint,
            expression_sidecar=state.sidecar,
            owning_ref=target,
            body=body,
            entity_refs=(ref.entity(owner),),
            aliases=(table,),
        )
        validate_bound_field(result, ref=target, registry=registry)
        if target.kind is not SemanticKind.TIME_DIMENSION:
            result = exact_unsigned(result)
        self.dependencies.append(result)
        if target.kind is SemanticKind.TIME_DIMENSION:
            axis = normalize_target_dimension(registry, path, prepared_column=path)
            consumed_zone = (
                self.consumption_timezone if instant_read else authority_zone or self.zone
            )
            prefix = (
                self.value(axis.parse.prefix, table, consumed_zone)
                if isinstance(axis.parse, HourPrefixParse)
                else None
            )
            prefix_input = (
                self.time_encodings.get(axis.parse.prefix)
                if isinstance(axis.parse, HourPrefixParse)
                else None
            )
            raw = result
            engine = entity_engine(
                registry, normalize_target_entity(registry, axis.entity_ref.path)
            )
            result, authority = source_time(
                result,
                axis,
                boundary_timezone="UTC",
                source_timezone=effective_source_timezone(registry, axis),
                engine=engine,
                prefix=prefix,
            )
            raw_prefix = prefix_input[0] if prefix_input is not None else None
            prefix_axis = prefix_input[1] if prefix_input is not None else None
            self.time_encodings[path] = (raw, axis, authority, raw_prefix, prefix_axis)
            self.source_times.extend(
                capture_source_times((authority,), consumption_timezone=consumed_zone)
            )
            if authority.kind == "civil_date":
                contribution = self.contribution
                validate_date_contribution(contribution, path, registry)
                if instant_read or (
                    contribution.time is not None and contribution.time.field.ref.path == path
                ):
                    result = localize(authority.timezone, timestamp(result))
            if authority.kind != "civil_date" and result.type().timezone is None:
                scale = result.type().scale
                if scale is not None and scale > 6:
                    raise compilation_error(
                        "exact native timezone conversion precision",
                        "submicrosecond conversion unsupported",
                    )
                result = localize("UTC", timestamp(result))
            if (
                instant_read
                and isinstance(axis.parse, StrptimeParse)
                and authority.kind != "instant"
            ):
                result = encoded_time(raw, result, axis.parse.format, authority.timezone)
        return result

    def period_condition(
        self,
        time: ir.Value,
        start: datetime | None,
        end: datetime,
        zone: str,
        *,
        use: TimePredicateUse = "value",
    ) -> ir.BooleanValue:
        contribution, registry = self.contribution, self.registry
        encoding = (
            self.time_encodings.get(contribution.time.field.ref.path) if contribution.time else None
        )
        if encoding is not None:
            raw, axis, authority, prefix, prefix_axis = encoding
            self.source_times.extend(capture_source_times((authority,), consumption_timezone=zone))
            condition = source_time_predicate(
                raw,
                axis,
                authority,
                engine=entity_engine(
                    registry, normalize_target_entity(registry, axis.entity_ref.path)
                ),
                start=start,
                end=end,
                prefix=prefix,
                prefix_axis=prefix_axis,
                use=use,
            )
            if condition is not None:
                return condition
        upper = time < temporal_bound(time, end, zone)
        return upper if start is None else upper & (time >= temporal_bound(time, start, zone))
