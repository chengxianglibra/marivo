"""Frozen non-time profile matrix using real shared project builders."""

import subprocess
import sys
from decimal import Decimal

import duckdb
import pytest

from tests.deviation_r82_oracle import PROFILES
from tests.shared_fixtures import DslCaseFactory, export_dsl_parquet_models


@pytest.mark.runtime
@pytest.mark.parametrize(
    "domain,key_profile",
    (("entity", "KS"), ("entity", "KI"), ("entity", "KC"), ("scalar", "KT"), ("category", "KT")),
)
@pytest.mark.parametrize("form", ("table", "parquet"))
def test_non_time_domains_types_keys_in_independent_processes(
    analysis_dsl_case_factory: DslCaseFactory, domain: str, key_profile: str, form: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        if key_profile == "KI":
            db.execute('ALTER TABLE "order" ALTER order_id TYPE BIGINT USING 1')
        if key_profile == "KC":
            db.execute('ALTER TABLE "order" ADD COLUMN member_seq BIGINT')
            db.execute("ALTER TABLE order_line ADD COLUMN order_seq BIGINT")
        for i, key in enumerate(("a", "b", "c"), 1):
            db.execute(
                'INSERT INTO "order" (order_id, channel) VALUES (?, ?)',
                [i if key_profile == "KI" else key, key],
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
                    Decimal((0, (digit,), -scale))
                    if index > 1
                    else digit / 10
                    if index == 1
                    else digit
                )
                db.execute(
                    f'UPDATE "order" SET profile_{index}=? WHERE channel=?', [value, "abc"[i - 1]]
                )
            additions.append(
                f"profile_{index} = ms.measure_column(name='profile_{index}', entity=orders, column='profile_{index}', additivity=ms.additive_all(), unit='CNY')\n"
            )
    models = case.root / "models/semantic/sales/models.py"
    body = models.read_text()
    if key_profile == "KC":
        body = body.replace("primary_key=['order_id']", "primary_key=['order_id', 'member_seq']")
        body = body.replace(
            "line_order = ms.relationship",
            "order_seq = ms.dimension_column(name='member_seq', entity=orders, column='member_seq')\nline_seq = ms.dimension_column(name='order_seq', entity=lines, column='order_seq')\nline_order = ms.relationship",
        )
        body = body.replace(
            "keys=[ms.join_on(line_order_id, order_id)]",
            "keys=[ms.join_on(line_order_id, order_id), ms.join_on(line_seq, order_seq)]",
        )
    models.write_text(body + "\n" + "".join(additions))
    if form == "parquet":
        export_dsl_parquet_models(case, case.root)
    for phase in ("produce", "fixed", "cold"):
        if phase == "fixed":
            case.database_path.rename(case.database_path.with_suffix(".offline"))
            for source in (case.root / "source_files").glob("*.parquet"):
                source.rename(source.with_suffix(".offline"))
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.deviation_r82_matrix_worker",
                str(case.root),
                phase,
                domain,
                key_profile,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        assert f'"accepted": "{phase}"' in process.stdout
