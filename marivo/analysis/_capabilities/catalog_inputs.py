"""Existing native catalog read inputs, retained by reference during Dataset assembly."""

from dataclasses import replace

from marivo.analysis._capabilities.model import ReadCapability
from marivo.semantic._capabilities.catalog_members import CATALOG_MEMBER_CONTRACTS


def _catalog_inputs() -> tuple[ReadCapability, ...]:
    descriptors: list[ReadCapability] = []
    catalog_specs: tuple[tuple[str, str, str, str], ...] = (
        *(
            (
                f"catalog.{member.property_name}",
                f"catalog.{member.property_name}",
                f"catalog.{member.property_name}",
                f"Browse catalog {member.property_name}.",
            )
            for member in CATALOG_MEMBER_CONTRACTS
        ),
        (
            "catalog.require",
            "catalog.require(ref)",
            "catalog.require",
            "Require one exact ref in the compiled catalog.",
        ),
        (
            "catalog.readiness",
            "catalog.readiness(refs=...)",
            "catalog.readiness",
            "Check semantic readiness for refs.",
        ),
    )

    for cap_id, entrypoint, target, summary in catalog_specs:
        descriptors.append(
            ReadCapability(
                id=cap_id,
                public_entrypoint=entrypoint,
                help_target=target,
                summary=summary,
                constraint_ids=(),
                callable_path=f"marivo.semantic.catalog.SemanticCatalog.{cap_id.split('.', 1)[1]}",
                receiver_family="SemanticCatalog",
                result_kind="immutable_metadata",
                read_bound="bounded",
            )
        )

    temporal_catalog_reads = (
        ReadCapability(
            id="catalog.period_calendars.grain",
            public_entrypoint="calendar.grain(level)",
            help_target="calendar.grain",
            summary="Return the governed Grain for one declared calendar level.",
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.PeriodCalendarEntry.grain",
            receiver_family="PeriodCalendarEntry",
            result_kind="immutable_metadata",
            read_bound="bounded",
            output_type="Grain",
        ),
        ReadCapability(
            id="catalog.period_calendars.period",
            public_entrypoint="calendar.period(level, key)",
            help_target="calendar.period",
            summary="Return one exact certified TimeScope for a named calendar period.",
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.PeriodCalendarEntry.period",
            receiver_family="PeriodCalendarEntry",
            result_kind="immutable_metadata",
            read_bound="bounded",
            produced_input_family="TimeScopeInput",
            output_type="TimeScope",
        ),
        ReadCapability(
            id="catalog.period_calendars.period_on",
            public_entrypoint="calendar.period_on(level, value)",
            help_target="calendar.period_on",
            summary="Return the exact certified TimeScope containing one civil date.",
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.PeriodCalendarEntry.period_on",
            receiver_family="PeriodCalendarEntry",
            result_kind="immutable_metadata",
            read_bound="bounded",
            produced_input_family="TimeScopeInput",
            output_type="TimeScope",
        ),
        ReadCapability(
            id="catalog.period_calendars.periods",
            public_entrypoint="calendar.periods(level, limit=20, cursor=None)",
            help_target="calendar.periods",
            summary="Browse one bounded page of certified periods for a calendar level.",
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.PeriodCalendarEntry.periods",
            receiver_family="PeriodCalendarEntry",
            result_kind="immutable_metadata",
            read_bound="bounded",
            output_type="CalendarPeriodPage",
        ),
        ReadCapability(
            id="catalog.temporal_sets.occurrence",
            public_entrypoint="temporal_set.occurrence(key)",
            help_target="temporal_set.occurrence",
            summary="Return one exact certified TimeScope for a named temporal occurrence.",
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.TemporalSetEntry.occurrence",
            receiver_family="TemporalSetEntry",
            result_kind="immutable_metadata",
            read_bound="bounded",
            produced_input_family="TimeScopeInput",
            output_type="TimeScope",
        ),
        ReadCapability(
            id="catalog.temporal_sets.occurrences",
            public_entrypoint="temporal_set.occurrences(limit=20, cursor=None)",
            help_target="temporal_set.occurrences",
            summary="Browse one bounded filtered page of certified temporal occurrences.",
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.TemporalSetEntry.occurrences",
            receiver_family="TemporalSetEntry",
            result_kind="immutable_metadata",
            read_bound="bounded",
            output_type="TemporalOccurrencePage",
        ),
    )
    descriptors.extend(temporal_catalog_reads)

    examples = {
        "catalog.require": 'result = catalog.require(ms.ref.metric("sales.revenue"))',
        "catalog.readiness": 'result = catalog.readiness(refs=[ms.ref.metric("sales.revenue")])',
        "catalog.period_calendars.grain": 'result = calendar.grain("fiscal_week")',
        "catalog.period_calendars.period": 'scope = calendar.period("fiscal_week", "FY2026-W01")',
        "catalog.period_calendars.period_on": 'from datetime import date\nscope = calendar.period_on("fiscal_week", date(2026, 1, 1))',
        "catalog.period_calendars.periods": 'result = calendar.periods("fiscal_week", limit=5)',
        "catalog.temporal_sets.occurrence": 'scope = temporal_set.occurrence("launch")',
        "catalog.temporal_sets.occurrences": "result = temporal_set.occurrences(limit=5)",
    }
    prefix = "import marivo.analysis as mv\nimport marivo.semantic as ms\nsession = mv.session.get_or_create('catalog-inspection')\ncatalog = session.catalog\n"
    result = []
    for descriptor in descriptors:
        preparation = ""
        if descriptor.receiver_family == "PeriodCalendarEntry":
            preparation = 'calendar = catalog.require(ms.ref.period_calendar("sales.fiscal"))\n'
        elif descriptor.receiver_family == "TemporalSetEntry":
            preparation = 'temporal_set = catalog.require(ms.ref.temporal_set("sales.campaigns"))\n'
        example = examples.get(descriptor.id, "result = " + descriptor.public_entrypoint)
        result.append(replace(descriptor, example=prefix + preparation + example))
    return tuple(result)


CATALOG_INPUTS = _catalog_inputs()
