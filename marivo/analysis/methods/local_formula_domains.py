"""Build the checked output and Population domains of a local formula."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

from marivo._definition.correspondence import coordinate_matches
from marivo._definition.expressions import (
    BuiltinSampleCoordinate,
)
from marivo._definition.relations import (
    PopulationDependency,
    PopulationProgram,
    population_keys,
)
from marivo._definition.types import AxisBinding, CalendarCoordinate, IntervalCoordinate
from marivo.analysis.binding.calendar_keys import (
    match_time_keys,
    require_window_endpoints,
    time_key_series,
)
from marivo.analysis.binding.programs import FormulaProgram, MemberInput
from marivo.analysis.core.data_validation import distinct_key_rows
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.semantic.spi import numeric_failure


def initial_formula_domain(
    program: FormulaProgram,
    inputs: list[pd.DataFrame],
    key_slots: tuple[str, ...],
    forced_domain: pd.DataFrame | None,
    report_candidates: pd.DataFrame | None,
) -> pd.DataFrame:
    keys = list(key_slots)
    if keys:
        domain_parts = []
        for frame in inputs:
            present = next(
                (
                    name
                    for name in (
                        f"__sample_scope_{len(keys)}",
                        "__sample_present",
                        "__numeric_present",
                    )
                    if name in frame
                ),
                None,
            )
            selected = (
                frame
                if present is None
                else frame.loc[frame[present].astype("boolean").fillna(False)]
            )
            if all(key in selected for key in keys):
                domain_parts.append(selected.loc[:, keys])
        domain = (
            distinct_key_rows(pd.concat(domain_parts, ignore_index=True), key_slots)
            if domain_parts
            else pd.DataFrame(columns=keys)
        )
        domain = domain.reset_index(drop=True)
    else:
        domain = pd.DataFrame(index=pd.RangeIndex(1))
        if program.output_population is not None and inputs:
            has_required_coordinate = any(
                bool(frame["__sample_scope_0"].astype("boolean").fillna(False).any())
                if "__sample_scope_0" in frame.columns
                else bool(frame["__sample_present"].astype("boolean").fillna(False).any())
                if "__sample_present" in frame.columns
                else bool(len(frame))
                for frame in inputs
            )
            if not has_required_coordinate:
                domain = domain.iloc[:0].copy()
    if forced_domain is not None:
        domain = (
            distinct_key_rows(forced_domain.loc[:, keys], key_slots)
            if keys
            else pd.DataFrame(index=pd.RangeIndex(len(forced_domain)))
        )
    if report_candidates is not None and keys:
        domain = distinct_key_rows(
            pd.concat([domain, report_candidates.loc[:, keys]], ignore_index=True), key_slots
        )
    return domain


@dataclass(slots=True)
class FormulaDomains:
    program: FormulaProgram
    key_slots: tuple[str, ...]
    population_frames: tuple[pd.DataFrame, ...]
    domain: pd.DataFrame

    def population_domain(
        self, definition: PopulationDependency, slots: tuple[str, ...]
    ) -> pd.DataFrame:

        if isinstance(definition, PopulationProgram):
            found = next(
                (
                    (binding, frame)
                    for binding, frame in zip(
                        self.program.populations, self.population_frames, strict=True
                    )
                    if binding.definition == definition
                ),
                None,
            )
            if found is not None:
                binding, frame = found
                selected = frame.loc[:, list(binding.keys)].copy()
                selected.columns = list(slots)
                return selected
        finite = population_keys(definition)
        if finite is None:
            numeric_failure(
                "a prepared independent Population domain", "missing source domain input"
            )
        records = [
            tuple(
                component
                for axis, value in zip(definition.axes, row, strict=True)
                for component in (
                    value
                    if isinstance(axis, AxisBinding)
                    and axis.role == "identity"
                    and isinstance(value, tuple)
                    else (value,)
                )
            )
            for row in finite
        ]
        return pd.DataFrame(records, columns=slots, dtype=object)

    def parent_scoped(self, rows: pd.DataFrame) -> pd.DataFrame:
        program = self.program
        if program.parent_scope is None:
            return rows
        allowed = self.population_domain(program.parent_scope, program.parent_slots)
        if not program.parent_slots:
            return rows if len(allowed) else rows.iloc[:0].copy()
        return rows.merge(
            distinct_key_rows(allowed, program.parent_slots),
            on=list(program.parent_slots),
            how="inner",
        )

    def count_coordinates(self, count: MemberInput, members: pd.DataFrame) -> pd.DataFrame:
        program, key_slots, domain = self.program, self.key_slots, self.domain
        keys = list(key_slots)
        mapping: list[tuple[str, str]] = []
        time_domains: list[tuple[str, CalendarCoordinate | IntervalCoordinate]] = []
        time_matches: list[
            tuple[
                str,
                CalendarCoordinate | IntervalCoordinate,
                CalendarCoordinate | IntervalCoordinate,
            ]
        ] = []
        actual_sample_slots: list[str] = []
        offset = 0
        for axis in program.axes:
            if isinstance(axis, BuiltinSampleCoordinate):
                actual_sample_slots.append(key_slots[offset])
                offset += 1
                continue
            match = next(
                (
                    (candidate, group)
                    for candidate, group in count.axis_mapping
                    if coordinate_matches(candidate, axis)
                ),
                None,
            )
            if match is None:
                assert isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
                time_domains.append((key_slots[offset], axis))
                offset += 1
                continue
            member_axis, source_keys = match
            if isinstance(axis, (CalendarCoordinate, IntervalCoordinate)):
                assert isinstance(member_axis, (CalendarCoordinate, IntervalCoordinate))
                time_matches.append((key_slots[offset], member_axis, axis))
            mapping.extend(
                zip(source_keys, key_slots[offset : offset + len(source_keys)], strict=True)
            )
            offset += len(source_keys)
        selected = members.loc[:, [source for source, _ in mapping]].copy()
        selected.columns = [target for _, target in mapping]
        for slot, member_axis, axis in time_matches:
            selected[slot] = match_time_keys(selected[slot], member_axis, axis)
            if axis is program.calendar and isinstance(axis, IntervalCoordinate):
                # A report can select one interval from a larger captured directory.
                selected = selected.loc[selected[slot].eq(axis.report_key)]
            elif member_axis != axis:
                selected = selected.dropna(subset=[slot])
        if time_domains:
            # Independent counts broadcast only over established complete keys.
            # The calendar directory describes positions, not represented rows.
            support_keys = [key for key in keys if key not in actual_sample_slots]
            support = domain.loc[:, support_keys].drop_duplicates()
            shared = [key for key in support_keys if key in selected]
            selected = (
                support.merge(selected, on=shared, how="inner")
                if shared
                else support.merge(selected, how="cross")
            )
            for slot, axis in time_domains:
                selected[slot] = selected[slot].astype(time_key_series(axis).dtype)
        if actual_sample_slots:
            ordinary_slots = [slot for slot in keys if slot not in actual_sample_slots]
            actual = domain.loc[:, keys]
            selected = (
                actual.merge(selected, on=ordinary_slots, how="inner")
                if ordinary_slots
                else actual.merge(selected, how="cross")
            )
        return selected

    def prepare_report(
        self,
        forced_domain: pd.DataFrame | None,
        sample_domains: Mapping[tuple[int, ...], pd.DataFrame] | None,
    ) -> dict[PopulationDependency, tuple[MemberInput, pd.DataFrame]]:
        program, key_slots, domain = self.program, self.key_slots, self.domain
        keys = list(key_slots)
        count_data: dict[PopulationDependency, tuple[MemberInput, pd.DataFrame]] = {}
        # Only an inferred report domain can gain keys from member counts.
        count_provides_domain = (
            bool(keys) and forced_domain is None and program.output_population is None
        )
        for count in program.counts:
            member_data = self.population_domain(count.population, count.keys)
            count_data[count.definition] = (count, member_data)
            if count_provides_domain:
                self.domain = domain
                member_coordinates = self.count_coordinates(count, member_data)
                member_coordinates = distinct_key_rows(member_coordinates.loc[:, keys], key_slots)
                if not member_coordinates.empty:
                    if domain.empty:
                        domain = member_coordinates.reset_index(drop=True)
                    else:
                        domain = distinct_key_rows(
                            pd.concat([domain, member_coordinates], ignore_index=True), key_slots
                        )

        if sample_domains and keys and forced_domain is None:
            parents = [
                value.loc[:, keys]
                for value in sample_domains.values()
                if all(key in value for key in keys)
            ]
            if parents:
                domain = distinct_key_rows(
                    pd.concat([domain, *parents], ignore_index=True), key_slots
                )
        domain = self.parent_scoped(domain)
        if program.output_population is not None:
            complete_domain = distinct_key_rows(
                self.parent_scoped(self.population_domain(program.output_population, key_slots)),
                key_slots,
            )
            if program.values and not program.population_checked:
                if keys:
                    missing = domain.merge(complete_domain, on=keys, how="left", indicator=True)
                    missing = missing.loc[missing["_merge"] == "left_only", keys]
                else:
                    missing = domain if not len(complete_domain) else domain.iloc[:0]
                if len(missing):
                    raise MaterializationError(
                        expected="an output Population covering every required complete coordinate",
                        received=f"missing output coordinates={missing.head(6).to_dict(orient='records') if keys else [()]}",
                        repair="Expand by=Population to include the missing complete keys, or explicitly restrict fact contributions before choosing the output domain.",
                        stage="population_coverage",
                        help_target="session.observe",
                    )
            domain = complete_domain

        if program.window_endpoints and program.calendar_key in domain:
            require_window_endpoints(domain[program.calendar_key], program.window_endpoints)
        self.domain = domain
        return count_data
