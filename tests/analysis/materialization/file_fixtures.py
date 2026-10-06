"""Shared builders for file fixtures tests."""

import csv
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from marivo.datasource.ir import CsvSourceIR, JsonSourceIR, ParquetSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import Case, SourceData


def author_file_case(
    case: Case,
    data: SourceData,
    profile: str,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> Path:
    assert isinstance(case.source, (CsvSourceIR, JsonSourceIR, ParquetSourceIR))
    path = Path(case.source.path)
    if profile == "parquet":
        pq.write_table(pa.Table.from_pylist(data.rows), path)
        source = f"md.parquet({str(path)!r})"
        parse = "ms.timestamp(timezone='UTC')"
    else:
        rows = [
            {
                key: value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else value
                for key, value in row.items()
            }
            for row in data.rows
        ]
        if profile == "csv":
            with path.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            source = f"md.csv({str(path)!r})"
            parse = "ms.timestamp(timezone='UTC')"
        else:
            assert profile == "local-json"
            path.write_text(json.dumps(rows))
            source = f"md.json({str(path)!r})"
            parse = "ms.timestamp(timezone='UTC')"
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse', path={case.session.datasource.fields['path']!r}, read_only=True)\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='R9', default=True)\n",
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + f"facts=ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source={source}, primary_key=['tenant','id','revision'])\n"
            + "amount=ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())\n"
            + f"happened=ms.time_dimension_column(name='happened', entity=facts, column='happened', granularity='second', parse={parse}, is_default=True)\n"
            + "total=ms.aggregate(name='total', measure=amount, agg='sum', empty=ms.empty.zero())\n",
        }
    )
    return path
