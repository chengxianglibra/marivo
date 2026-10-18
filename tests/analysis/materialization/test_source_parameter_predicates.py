"""Parameter byte predicates retain rewritten nested relation ownership."""

import ibis
import pandas as pd

from marivo.analysis.compiler.source_predicates import parameter_string_comparisons
from marivo.analysis.materialization.query_parameters import _replace_parameter


def test_parameter_bytes_rebuild_nested_join_operands_before_outer_predicates() -> None:
    backend = ibis.duckdb.connect()
    try:
        source = backend.create_table(
            "labels", pd.DataFrame({"id": [1, 2, 3], "label": ["a", "A", "é"]})
        )
        parameter = ibis.table({"key": "string"}, name="selected")
        selected = source.inner_join(parameter, source.label.identical_to(parameter.key)).select(
            source.id, source.label
        )
        reference = selected.view()
        query = selected.inner_join(reference, selected.label.identical_to(reference.label)).select(
            selected.id, selected.label
        )
        postgres = parameter_string_comparisons(query, "selected", "postgres")
        sql = ibis.to_sql(postgres, dialect="postgres")
        assert "CONVERT_TO" in sql.upper() and "TO_JSON" in sql.upper()
        rewritten = parameter_string_comparisons(query, "selected", "duckdb")
        bound = _replace_parameter(rewritten, "selected", ibis.memtable({"key": ["a", "é"]}))
        frame = backend.execute(bound.order_by(bound.id))
        assert frame.id.tolist() == [1, 3]
        assert frame.label.tolist() == ["a", "é"]
    finally:
        backend.disconnect()
