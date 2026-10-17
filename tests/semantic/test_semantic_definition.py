"""Zero-execution expression descriptions surfaced through resolved value contracts."""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

import marivo.semantic as ms
from marivo.refs import SemanticKindTag
from marivo.semantic._deferred_semantics import consume_field, consume_predicate
from marivo.semantic._definition_expression import describe_expression
from marivo.semantic._definition_expression_types import (
    ExpressionDescription,
    _UnsupportedExpression,
)
from marivo.semantic.errors import ErrorKind, SemanticExpressionError

_DOMAIN_PY = "import marivo.semantic as ms\nms.domain(name='sales', owner='Owner')\n"
_MODEL = """
import marivo.datasource as md
import marivo.semantic as ms
rows = ms.entity(name='rows', datasource=ms.ref.datasource('warehouse'), source=md.table('records'), primary_key=['id'])
day = ms.time_dimension_column(name='day', entity=rows, column='activity_date', granularity='day', parse=ms.timestamp(timezone='UTC'), is_default=True)
region = ms.dimension_column(name='region', entity=rows, column='region')
spend = ms.measure_column(name='spend', entity=rows, column='spend_cny', additivity=ms.additive_all(), unit='CNY')

@ms.dimension(name='literal_values', entity=rows)
def literal_values(t):
    return (t.region == 'private-token-987').ifelse(73921, 0)

@ms.dimension(name='unsupported', entity=rows)
def unsupported(t):
    return t.region.upper()

finalized = ms.finalize()
rows_value = finalized.resolve(rows)
day_value = finalized.resolve(day)
spend_value = finalized.resolve(spend)

@ms.measure()
def scaled():
    return spend_value * 1234567

@ms.metric()
def total():
    return spend_value.sum(time=day_value)

@ms.metric()
def row_count():
    return rows_value.count()
"""


def _write_workspace(workspace: Path, models: str) -> Path:
    semantic_root = workspace / "models" / "semantic" / "sales"
    semantic_root.mkdir(parents=True, exist_ok=True)
    (semantic_root / "_domain.py").write_text(_DOMAIN_PY)
    (semantic_root / "models.py").write_text(models)
    datasource_path = workspace / "models" / "datasources" / "warehouse.py"
    datasource_path.parent.mkdir(parents=True, exist_ok=True)
    datasource_path.write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (workspace / "marivo.toml").write_text('[project]\nname = "test"\n')
    return workspace


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _write_workspace(tmp_path_factory.mktemp("semantic-definition"), textwrap.dedent(_MODEL))


@pytest.fixture(scope="module")
def catalog(workspace: Path) -> ms.SemanticCatalog:
    return ms.load(workspace_dir=workspace)


def _description(
    catalog: ms.SemanticCatalog, ref: ms.Ref[SemanticKindTag]
) -> ExpressionDescription:
    return catalog._state.sidecar.bodies[ref].description


def _expression_fact(catalog: ms.SemanticCatalog, ref: ms.Ref[SemanticKindTag]) -> str | None:
    facts = dict(catalog.describe(ref).facts)
    return facts.get("expression")


def test_restricted_body_literals_are_disclosed_as_normalized_expression(
    catalog: ms.SemanticCatalog,
) -> None:
    ref = ms.ref.dimension("sales.rows.literal_values")
    description = _description(catalog, ref)
    assert description.status == "supported"
    expression = description.expression
    assert expression.kind == "ifelse"
    assert expression.when_true.kind == "literal" and expression.when_true.value == 73921
    assert expression.condition.kind == "binary" and expression.condition.operator == "eq"

    text = _expression_fact(catalog, ref)
    assert text == "(t1['region'] == 'private-token-987').ifelse(73921, 0)"
    assert description.display is not None and description.display.text == text
    assert description.display.bindings == (("t1", ms.ref.entity("sales.rows")),)
    rendered = catalog.describe(ref).render()
    assert "private-token-987" in rendered and "73921" in rendered


def test_unsupported_structure_keeps_its_display_fact(catalog: ms.SemanticCatalog) -> None:
    ref = ms.ref.dimension("sales.rows.unsupported")
    description = _description(catalog, ref)
    assert description.status == "unsupported"
    assert description.reason == "unsupported_syntax"
    assert _expression_fact(catalog, ref) == "t1['region'].upper()"
    contract = catalog.describe(ref)
    assert contract.location is not None and "models.py:" in contract.location


def test_column_values_disclose_their_column_not_an_expression(
    catalog: ms.SemanticCatalog,
) -> None:
    facts = dict(catalog.describe(ms.ref.measure("sales.rows.spend")).facts)
    assert facts["column"] == "spend_cny"
    assert "expression" not in facts


def test_contract_reads_do_not_execute_or_print(
    catalog: ms.SemanticCatalog,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import ibis

    import marivo.analysis as mv
    from marivo.datasource.runtime import DatasourceConnectionService
    from marivo.semantic import _expression_binding

    references = [
        reference
        for collection in (catalog.entities, catalog.fields, catalog.metrics)
        for reference in collection.refs
    ]
    assert len(references) == 9

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("contract reading attempted execution or IO")

    with monkeypatch.context() as guard:
        guard.setattr(inspect, "getsource", forbidden)
        guard.setattr(Path, "read_text", forbidden)
        guard.setattr(Path, "write_text", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(ibis, "to_sql", forbidden)
        guard.setattr(_expression_binding, "_call_body", forbidden)
        guard.setattr(DatasourceConnectionService, "__init__", forbidden)
        guard.setattr(mv, "session", forbidden)
        for method in ("preview", "readiness", "source_health"):
            guard.setattr(ms.SemanticCatalog, method, forbidden)
        for reference in references:
            contract = catalog.describe(reference)
            rendered = contract.render(max_output_bytes=None)
            assert "boundary: definition only" in rendered
            assert "\n" not in repr(contract) and ".show()" in repr(contract)
    assert capsys.readouterr().out == ""


def test_description_does_not_change_fingerprint(
    workspace: Path, catalog: ms.SemanticCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.semantic import _expression_binding

    def unavailable(*args: object, **kwargs: object) -> _UnsupportedExpression:
        return _UnsupportedExpression("description_unavailable")

    monkeypatch.setattr(_expression_binding, "describe_expression", unavailable)
    current = ms.load(workspace_dir=workspace)
    ref = ms.ref.dimension("sales.rows.literal_values")
    assert current.definition_fingerprint == catalog.definition_fingerprint
    assert (
        consume_field(current.require(ref)).reference
        == consume_field(catalog.require(ref)).reference
    )
    assert _description(current, ref).status == "unsupported"
    assert _expression_fact(current, ref) is None
    assert _description(catalog, ref).status == "supported"
    assert _expression_fact(catalog, ref) is not None


def test_nonfinite_comparison_is_rejected_during_authoring(catalog: ms.SemanticCatalog) -> None:
    region = catalog.require(ms.ref.dimension("sales.rows.region"))
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(SemanticExpressionError) as exc:
            consume_predicate(region == value)
        assert exc.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
        assert exc.value.expected and exc.value.received
        assert exc.value.repair is not None and exc.value.repair.kind == "reauthor"


def test_expression_operator_tokens_are_syntax_faithful() -> None:
    def operator(expression: str) -> str:
        function = ast.parse("def value(t):\n    return " + expression).body[0]
        assert isinstance(function, ast.FunctionDef)
        result = describe_expression(
            function, entities={"t": ms.ref.entity("sales.rows")}, bindings={}
        )
        assert result.status == "supported" and result.expression.kind == "binary"
        return result.expression.operator

    assert operator("t.flags & 3") == "&"
    assert operator("t.first + t.last") == "+"


def test_supported_expression_matrix(tmp_path: Path) -> None:
    cases = [
        ('t["amount"]', "column"),
        ("+t.amount", "unary"),
        ("-t.amount", "unary"),
        ("~(t.amount == 0)", "unary"),
        ("t.amount + t.other", "binary"),
        ("t.amount - t.other", "binary"),
        ("t.amount * t.other", "binary"),
        ("t.amount / t.other", "binary"),
        ("(t.amount >= 0) & (t.other < 9)", "binary"),
        ("(t.amount != 0) | (t.other <= 9)", "binary"),
        ("(t.amount > 0) ^ (t.other == 9)", "binary"),
        ('t.amount.cast("int64")', "cast"),
        ("(t.amount > 0).ifelse(t.amount, None)", "ifelse"),
    ]
    definitions = "\n\n".join(
        f"@ms.dimension(name='value_{index}', entity=rows)\n"
        f"def value_{index}(t):\n    return {expression}"
        for index, (expression, _) in enumerate(cases)
    )
    models = (
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "rows = ms.entity(name='rows', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('rows'))\n" + definitions + "\n"
    )
    catalog = ms.load(workspace_dir=_write_workspace(tmp_path, models))
    for index, (expression, kind) in enumerate(cases):
        ref = ms.ref.dimension(f"sales.rows.value_{index}")
        description = _description(catalog, ref)
        assert description.status == "supported", expression
        assert description.expression.kind == kind, expression
        assert description.display is not None, expression
        text = _expression_fact(catalog, ref)
        assert text == description.display.text, expression
        ast.parse(text, mode="eval")


def test_description_limits_and_external_names() -> None:
    def describe(expression: str) -> str:
        function = ast.parse("def value(t):\n    return " + expression).body[0]
        assert isinstance(function, ast.FunctionDef)
        result = describe_expression(
            function, entities={"t": ms.ref.entity("sales.rows")}, bindings={}
        )
        assert result.status == "unsupported"
        return result.reason

    assert describe("t.amount * PRIVATE_VALUE") == "unsupported_syntax"
    assert describe('t.amount.cast("private-type-token")') == "unsupported_syntax"
    assert describe("+".join(["t.amount"] * 40)) == "limit_exceeded"
