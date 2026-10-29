"""Existing native catalog read inputs, retained by reference during Dataset assembly."""

from dataclasses import replace

from marivo.analysis._capabilities.model import ReadCapability


def _catalog_inputs() -> tuple[ReadCapability, ...]:
    descriptors: list[ReadCapability] = []
    temporal_catalog_reads = (
        ReadCapability(
            id="catalog.period_calendars.grain",
            public_entrypoint="calendar.grain(level)",
            help_target="calendar.grain",
            summary="Return the governed Grain for one declared calendar level.",
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.PeriodCalendar.grain",
            receiver_family="PeriodCalendar",
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
            callable_path="marivo.semantic.catalog.PeriodCalendar.period",
            receiver_family="PeriodCalendar",
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
            callable_path="marivo.semantic.catalog.PeriodCalendar.period_on",
            receiver_family="PeriodCalendar",
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
            callable_path="marivo.semantic.catalog.PeriodCalendar.periods",
            receiver_family="PeriodCalendar",
            result_kind="immutable_metadata",
            read_bound="bounded",
            output_type="CalendarPeriodPage",
        ),
        ReadCapability(
            id="catalog.temporal_sets.occurrence",
            public_entrypoint="temporal_set.occurrence(key)",
            help_target="temporal_set.occurrence",
            summary="Return one original half-open certified TimeScope with key, category, boundary timezone and snapshot identity; no source reads.",
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.TemporalSet.occurrence",
            receiver_family="TemporalSet",
            result_kind="immutable_metadata",
            read_bound="bounded",
            produced_input_family="TimeScopeInput",
            output_type="TimeScope",
        ),
        ReadCapability(
            id="catalog.temporal_sets.occurrences",
            public_entrypoint="temporal_set.occurrences(limit=20, cursor=None)",
            help_target="temporal_set.occurrences",
            summary=(
                "Browse certified occurrences by overlap without clipping their original bounds. "
                "Use date filters for civil-date sets and datetime filters for timestamp sets; "
                "explicit bounds must lie within certified coverage, whose timestamp boundaries "
                "are local midnights in the captured timezone. Omitted bounds leave that side unfiltered. "
                "category is an exact nonempty string or None for all categories. "
                "Pages are ordered by start, end and key; limit is 1..100. "
                "Follow next_cursor until None, keeping the same snapshot and filters. "
                "A covered empty page means no matching occurrences; unavailable authority rejects. "
                "page.show() displays keys, original bounds, categories, timezones and has_more."
            ),
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.TemporalSet.occurrences",
            receiver_family="TemporalSet",
            result_kind="immutable_metadata",
            read_bound="bounded",
            output_type="TemporalOccurrencePage",
        ),
    )
    descriptors.extend(temporal_catalog_reads)

    examples = {
        "catalog.period_calendars.grain": 'result = calendar.grain("fiscal_week")',
        "catalog.period_calendars.period": 'scope = calendar.period("fiscal_week", "FY2026-W01")',
        "catalog.period_calendars.period_on": 'from datetime import date\nscope = calendar.period_on("fiscal_week", date(2026, 1, 1))',
        "catalog.period_calendars.periods": 'result = calendar.periods("fiscal_week", limit=5)',
        "catalog.temporal_sets.occurrence": 'scope = temporal_set.occurrence("launch")',
        "catalog.temporal_sets.occurrences": "result = temporal_set.occurrences(limit=5)",
    }
    prefix = "catalog = session.catalog\n"
    requirements = {
        "catalog.period_calendars.grain": "Use an exact declared level. A current certified snapshot is captured when available; otherwise the result is an unbound lookup. Certify the calendar before using its Grain with scope.by; this lookup does not certify it.",
        "catalog.period_calendars.period": "Use an exact declared level and certified key from the current snapshot; missing keys or unavailable certification reject.",
        "catalog.period_calendars.period_on": "Use an exact declared level and a civil date covered by the current certified snapshot; missing coverage rejects.",
        "catalog.period_calendars.periods": "Use a certified level and limit=1..100, excluding bool. Pass page.next_cursor for the same snapshot and level; omit cursor for the first page. Continue until next_cursor is None.",
        "catalog.temporal_sets.occurrence": "Use an exact occurrence key from the current certified snapshot; unavailable certification or missing keys reject.",
        "catalog.temporal_sets.occurrences": "Use the current certified snapshot and preserve the selected filters when continuing a page.",
    }
    result = []
    for descriptor in descriptors:
        preparation = ""
        if descriptor.receiver_family == "PeriodCalendar":
            preparation = 'calendar = catalog.get("period_calendar:sales.fiscal")\nassert calendar.kind == "period_calendar"\n'
        elif descriptor.receiver_family == "TemporalSet":
            preparation = 'temporal_set = catalog.get("temporal_set:sales.campaigns")\nassert temporal_set.kind == "temporal_set"\n'
        example = examples.get(descriptor.id, "result = " + descriptor.public_entrypoint)
        result.append(
            replace(
                descriptor,
                example=prefix + preparation + example,
                acquisition=requirements[descriptor.id],
                related=("session.get_or_create", "semantic.preview")
                if descriptor.id == "catalog.period_calendars.grain"
                else ("session.get_or_create",),
            )
        )
    return tuple(result)


CATALOG_INPUTS = _catalog_inputs()
