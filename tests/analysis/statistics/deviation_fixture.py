"""Fixture-owned numeric, key and temporal profiles shared by journeys."""

import json
from datetime import datetime
from decimal import Decimal

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from tests.analysis.statistics.deviation_oracle import PROFILES
from tests.shared_fixtures import DslCase, export_dsl_parquet_models

ORIGINS = tuple(
    (form, unit, zone, calendar)
    for zone in ("UTC", "America/New_York")
    for form, units in (("table", ("us",)), ("parquet", ("s", "ms", "us", "ns")))
    for unit in units
    for calendar in (False, True)
    if not calendar or unit == "us"
)


# Cover key carriers separately from temporal/storage boundaries.
RECOVERY_PROFILES = tuple(
    (key, form, unit, zone, calendar)
    for key in ("KS", "KI", "KC")
    for form, unit, zone, calendar in ORIGINS
    if key == "KC" or (form, unit, zone, calendar) == ("parquet", "us", "UTC", False)
)


def prepare_profiles(
    case: DslCase,
    key_profile: str,
    form: str,
    unit: str,
    zone: str,
    calendar: bool,
    *,
    followup: bool = False,
) -> None:
    (case.root / "deviation-profile.json").write_text(
        json.dumps(
            {
                "key_profile": key_profile,
                "form": form,
                "unit": unit,
                "zone": zone,
                "calendar": calendar,
                "followup": followup,
            }
        )
    )
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        db.execute('ALTER TABLE "order" ALTER ordered_at TYPE TIMESTAMP')
        if key_profile == "KI":
            db.execute('ALTER TABLE "order" ALTER order_id TYPE BIGINT USING 1')
        if key_profile == "KC":
            db.execute('ALTER TABLE "order" ADD COLUMN member_seq BIGINT')
            db.execute("ALTER TABLE order_line ADD COLUMN order_seq BIGINT")
        for i, key in enumerate(("a", "b", "c"), 1):
            # Midday UTC lies inside the same date in both report zones.
            day = (1, 2, 4)[i - 1] if calendar else i
            db.execute(
                'INSERT INTO "order" (order_id, channel, ordered_at) VALUES (?, ?, ?)',
                [i if key_profile == "KI" else key, key, datetime(2026, 8, day, 12)],
            )
            if key_profile == "KC":
                db.execute('UPDATE "order" SET member_seq=? WHERE order_id=?', [10 + i, key])
        additions = []
        for index, profile in enumerate(PROFILES):
            dtype = "BIGINT" if index == 0 else "DOUBLE" if index == 1 else profile.upper()
            db.execute(f'ALTER TABLE "order" ADD COLUMN profile_{index} {dtype}')
            scale = int(profile.split(",")[1][:-1]) if index > 1 else 0
            for i, digit in enumerate((1, 2, 7), 1):
                value = (
                    Decimal((0, (digit,), -scale)) if index > 1 else digit / 10 if index else digit
                )
                db.execute(
                    f'UPDATE "order" SET profile_{index}=? WHERE channel=?', [value, "abc"[i - 1]]
                )
            additions.append(
                f"profile_{index} = ms.measure_column(name='profile_{index}', entity=orders, column='profile_{index}', additivity=ms.additive_all(), unit='CNY')\n"
                f"maximum_{index} = ms.aggregate(name='maximum_{index}', measure=profile_{index}, agg='max', time=ordered_at)\n"
            )
            if followup:
                additions.append(
                    f"total_{index} = ms.aggregate(name='total_{index}', measure=profile_{index}, agg='sum', time=ordered_at, empty=ms.empty.zero())\n"
                )
    models = case.root / "models/semantic/sales/models.py"
    body = models.read_text()
    if key_profile == "KC":
        body = body.replace("primary_key=['order_id']", "primary_key=['order_id', 'member_seq']")
        body = body.replace(
            "line_order = ms.relationship",
            "order_seq = ms.dimension_column(name='member_seq', entity=orders, column='member_seq')\nline_seq = ms.dimension_column(name='order_seq', entity=lines, column='order_seq')\nline_order = ms.relationship",
        ).replace(
            "keys=[ms.join_on(line_order_id, order_id)]",
            "keys=[ms.join_on(line_order_id, order_id), ms.join_on(line_seq, order_seq)]",
        )
    if calendar:
        coverage_start = (
            "__import__('datetime').date(2026, 7, 26)"
            if followup
            else "__import__('datetime').date(2026, 8, 1)"
        )
        body += (
            "\ncalendar = ms.entity(name='calendar', datasource=warehouse, source=md.table('calendar', columns={'calendar_date': 'calendar_date', 'period': 'period'}), primary_key=['calendar_date'])\n"
            "calendar_date = ms.time_dimension_column(name='calendar_date', entity=calendar, column='calendar_date', granularity='day')\n"
            "period = ms.dimension_column(name='period', entity=calendar, column='period')\n"
            f"unequal = ms.period_calendar(name='unequal', date=calendar_date, boundary_timezone={zone!r}, coverage=({coverage_start}, __import__('datetime').date(2026, 8, 7)), levels={{'period': period}})\n"
        )
        with duckdb.connect(str(case.database_path)) as db:
            db.execute("CREATE TABLE calendar(calendar_date DATE, period VARCHAR)")
            for day in range(1, 7):
                db.execute(
                    "INSERT INTO calendar VALUES (?, ?)",
                    [f"2026-08-{day:02}", "abc"[0 if day == 1 else 1 if day <= 3 else 2]],
                )
            if followup:
                for day in range(26, 32):
                    db.execute(
                        "INSERT INTO calendar VALUES (?, ?)",
                        [f"2026-07-{day:02}", "xyz"[0 if day == 26 else 1 if day <= 28 else 2]],
                    )
    models.write_text(body + "\n" + "".join(additions))
    if form == "parquet":
        export_dsl_parquet_models(case, case.root)
        path = case.root / "source_files/order.parquet"
        table = pq.read_table(path)
        column = table.schema.get_field_index("ordered_at")
        table = table.set_column(column, "ordered_at", table["ordered_at"].cast(pa.timestamp(unit)))
        pq.write_table(table, path)
        assert pq.read_schema(path).field("ordered_at").type == pa.timestamp(
            "ms" if unit == "s" else unit
        )
        if calendar:
            with duckdb.connect(str(case.database_path)) as db:
                calendar_table = db.execute("SELECT * FROM calendar").fetch_arrow_table()
            path = case.root / "source_files/calendar.parquet"
            pq.write_table(calendar_table, path)
            models.write_text(
                models.read_text().replace(
                    "md.table('calendar', columns=", f"md.parquet({str(path)!r}, columns="
                )
            )
