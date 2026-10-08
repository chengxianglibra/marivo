"""Decimal carrier boundaries through source, offline fixed and cold kernels."""

import subprocess
import sys
from decimal import Decimal

import duckdb
import pytest

from tests.shared_fixtures import DslCaseFactory, export_dsl_parquet_models


@pytest.mark.runtime
@pytest.mark.parametrize(
    "precision,scale,form",
    (
        (1, 0, "parquet"),
        (1, 1, "parquet"),
        (9, 2, "parquet"),
        (18, 6, "parquet"),
        (38, 0, "parquet"),
        (38, 18, "parquet"),
        (38, 38, "parquet"),
        (38, 6, "table"),
        (38, 38, "table"),
    ),
)
def test_decimal_carrier_boundaries_in_independent_processes(
    analysis_dsl_case_factory: DslCaseFactory, precision: int, scale: int, form: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    models = case.root / "models/semantic/sales/models.py"
    declarations = []
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        db.execute('ALTER TABLE "order" ADD COLUMN member_seq BIGINT')
        db.execute("ALTER TABLE order_line ADD COLUMN order_seq BIGINT")
        for index, key in enumerate(("law_a", "law_b", "law_c", "law_null"), 1):
            db.execute('INSERT INTO "order" (order_id, member_seq) VALUES (?, ?)', [key, index])
        column = f"law_{scale}"
        db.execute(f'ALTER TABLE "order" ADD COLUMN {column} DECIMAL({precision},{scale})')
        for key, digit in zip(("law_a", "law_b", "law_c"), (1, 2, 7), strict=True):
            value = Decimal((0, (digit,), -scale))
            db.execute(f'UPDATE "order" SET {column}=? WHERE order_id=?', [value, key])
        declarations.append(
            f"{column} = ms.measure_column(name={column!r}, entity=orders, column={column!r}, "
            "additivity=ms.additive_all(), unit='CNY')\n"
        )
    body = models.read_text().replace(
        "primary_key=['order_id']", "primary_key=['order_id', 'member_seq']"
    )
    body = body.replace(
        "line_order = ms.relationship",
        "order_seq = ms.dimension_column(name='member_seq', entity=orders, column='member_seq')\nline_seq = ms.dimension_column(name='order_seq', entity=lines, column='order_seq')\nline_order = ms.relationship",
    ).replace(
        "keys=[ms.join_on(line_order_id, order_id)]",
        "keys=[ms.join_on(line_order_id, order_id), ms.join_on(line_seq, order_seq)]",
    )
    models.write_text(body + "\n" + "".join(declarations))
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
                "tests.analysis.statistics.deviation_decimal_worker",
                str(case.root),
                str(precision),
                str(scale),
                phase,
            ],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        assert f'"accepted": "{phase}"' in process.stdout
