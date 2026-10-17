"""Pure certified-calendar fixture shared by temporal export and interval journeys."""

from datetime import date

import marivo.semantic as ms
from marivo._temporal import (
    TemporalSnapshotStore,
    certify_period_calendar_rows,
    period_calendar_definition_digest,
)
from marivo.semantic._definition_identity import scoped_definition_fingerprint


def publish_calendar(catalog: ms.SemanticCatalog, zone: str, *, baseline: bool = False) -> None:
    ref = ms.ref.period_calendar("sales.unequal")
    calendar = catalog._require_ready().period_calendars[ref.path]
    snapshot = certify_period_calendar_rows(
        calendar_ref=ref,
        boundary_timezone=zone,
        coverage=(date(2026, 7, 26) if baseline else date(2026, 8, 1), date(2026, 8, 7)),
        columns=("calendar_date", "period"),
        retained_values=tuple(
            {
                "calendar_date": f"2026-07-{day:02}",
                "period": "xyz"[0 if day == 26 else 1 if day <= 28 else 2],
            }
            for day in range(26, 32)
            if baseline
        )
        + tuple(
            {
                "calendar_date": f"2026-08-{day:02}",
                "period": "abc"[0 if day == 1 else 1 if day <= 3 else 2],
            }
            for day in range(1, 7)
        ),
        date_column="calendar_date",
        levels={"period": "period"},
    )
    dependency = scoped_definition_fingerprint(
        root=ref,
        definitions=catalog._state.definitions,
        dependencies=catalog._state.dependencies,
        sidecar=catalog._state.sidecar,
    )
    TemporalSnapshotStore(catalog.workspace_dir).publish(
        snapshot,
        definition_digest=period_calendar_definition_digest(
            calendar_ref=ref,
            boundary_timezone=zone,
            coverage=calendar.coverage,
            levels=calendar.levels,
            correspondences=calendar.correspondences,
            dependency_digest=dependency,
        ),
    )
