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
    for calendar in (False,)
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
    profile_indices: tuple[int, ...] = tuple(range(len(PROFILES))),
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
                "profiles": profile_indices,
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
        metrics = []
        for index in profile_indices:
            profile = PROFILES[index]
            dtype = "BIGINT" if index == 0 else "DOUBLE" if index == 1 else profile.upper()
            db.execute(f'ALTER TABLE "order" ADD COLUMN profile_{index} {dtype}')
            scale = int(profile.split(",")[1][:-1]) if index > 1 else 0
            db.execute(
                f'UPDATE "order" SET profile_{index}=CASE channel '
                "WHEN 'a' THEN ? WHEN 'b' THEN ? WHEN 'c' THEN ? END",
                [
                    Decimal((0, (digit,), -scale)) if index > 1 else digit / 10 if index else digit
                    for digit in (1, 2, 7)
                ],
            )
            additions.append(
                f"profile_{index} = ms.measure_column(name='profile_{index}', entity=orders, column='profile_{index}', additivity=ms.additive_all(), unit='CNY')\n"
            )
            metrics.append(
                f"profile_{index}_value = profile_{index}\n"
                f"@ms.metric()\ndef maximum_{index}():\n"
                f"    return profile_{index}_value.max(time=time_value)\n"
            )
            if followup:
                metrics.append(
                    f"@ms.metric()\ndef total_{index}():\n"
                    f"    return profile_{index}_value.sum(time=time_value)\n"
                )
    models = case.root / "models/semantic/sales/models.py"
    body, composition = models.read_text().split("# Named expressions", 1)
    if key_profile == "KC":
        body = body.replace("primary_key=['order_id']", "primary_key=['order_id', 'member_seq']")
        body = body.replace(
            "line_order = ms.relationship",
            "order_seq = ms.dimension_column(name='member_seq', entity=orders, column='member_seq')\nline_seq = ms.dimension_column(name='order_seq', entity=lines, column='order_seq')\nline_order = ms.relationship",
        ).replace(
            "keys=[ms.join_on(line_order_id, order_id)]",
            "keys=[ms.join_on(line_order_id, order_id), ms.join_on(line_seq, order_seq)]",
        )
    pass
    models.write_text(
        body
        + "\n"
        + "".join(additions)
        + "\n# Named expressions"
        + composition
        + "\n"
        + "".join(metrics)
    )
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
        pass
