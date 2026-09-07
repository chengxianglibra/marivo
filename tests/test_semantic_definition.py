"""Independent consumer contracts for the zero-execution definition projection."""

from __future__ import annotations

import inspect
import json
import textwrap
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import marivo.semantic as ms
from marivo._help import help as marivo_help
from marivo.semantic.definition import SemanticDefinition
from marivo.semantic.reader import SemanticProject

Factory = Callable[[dict[str, str]], SemanticProject]

_MODEL = """
import marivo.datasource as md
import marivo.semantic as ms
import marivo.analysis as mv
rows = ms.entity(name='rows', datasource=ms.ref.datasource('warehouse'), source=md.table('records'))
time = ms.time_dimension_column(name='day', entity=rows, column='activity_date', granularity='day', parse=ms.datetime(timezone='Asia/Shanghai'))
region = ms.dimension_column(name='region', entity=rows, column='region')
@ms.measure(name='spend', entity=rows, additivity='additive', unit='CNY')
def spend(t):
    return t.spend_cny.cast('float64')
@ms.measure(name='weight', entity=rows, additivity='additive')
def weight(t):
    return t.weight
@ms.measure(name='state', entity=rows, additivity=ms.semi_additive(over=time, fold=('percentile', 0.95)))
def state(t):
    return t.state
@ms.dimension(name='redacted', entity=rows)
def redacted(t):
    return (t.region == 'private-token-987').ifelse(73921, 0)
@ms.dimension(name='unsupported', entity=rows)
def unsupported(t):
    return t.region.upper()
@ms.measure(name='bound', entity=rows, additivity='additive')
def bound(t):
    return ms.bind(spend, t) * 1234567
@ms.metric(name='custom', entities=[rows], additivity='additive')
def custom(t):
    return t.spend_cny.sum()
total = ms.aggregate(name='total', measure=spend, agg='sum', unit='CNY')
p95 = ms.aggregate(name='p95', measure=spend, agg=('percentile', 0.95))
count = ms.count(name='count', entity=rows, filter=ms.where(region=('east', 'west')))
filtered = ms.aggregate(name='filtered', measure=spend, agg='sum', filter=ms.where(region=True))
weighted = ms.weighted_mean(name='weighted', value=spend, weight=weight)
ratio = ms.ratio(name='ratio', numerator=total, denominator=count)
repeated = ms.linear(name='repeated', add=[total, total], subtract=[count])
all_time = ms.cumulative(name='all_time', base=total)
mtd = ms.cumulative(name='mtd', base=total, over=time, anchor=ms.grain_to_date(grain=mv.grain('month')))
rolling = ms.cumulative(name='rolling', base=total, over=time, anchor=ms.trailing(count=3, unit='day'))
retail = ms.cumulative(name='retail', base=total, over=time, anchor=ms.grain_to_date(grain=ms.calendar_grain(calendar=ms.ref.period_calendar('commerce.retail'), level='retail_quarter')))
state_total = ms.aggregate(name='state_total', measure=state, agg='sum')
state_override = ms.aggregate(name='state_override', measure=state, agg='sum', fold='last')
gross_profit = ms.linear(name='gross_profit', add=[total], subtract=[filtered, total, filtered, total])
nested_profit = ms.linear(name='nested_profit', add=[gross_profit, total])
state_linear = ms.linear(name='state_linear', add=[state_total, state_override])
ratio_linear = ms.linear(name='ratio_linear', add=[ratio, ratio])
cumulative_linear = ms.linear(name='cumulative_linear', add=[all_time, mtd])
percentile_linear = ms.linear(name='percentile_linear', add=[p95, p95])
"""
_CALENDAR = """
from datetime import date
import marivo.datasource as md
import marivo.semantic as ms
rows = ms.entity(name='dates', datasource=ms.ref.datasource('warehouse'), source=md.table('dates'))
day = ms.time_dimension_column(name='day', entity=rows, column='day', granularity='day')
quarter = ms.dimension_column(name='quarter', entity=rows, column='quarter')
calendar = ms.period_calendar(name='retail', date=day, boundary_timezone='Asia/Shanghai', coverage=(date(2025, 1, 1), date(2027, 1, 1)), levels={'retail_quarter': quarter})
"""


@pytest.fixture
def catalog(semantic_project_factory: Factory) -> ms.SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Owner')\n",
            "sales/models.py": textwrap.dedent(_MODEL),
            "commerce/_domain.py": "import marivo.semantic as ms\nms.domain(name='commerce', owner='Owner')\n",
            "commerce/calendar.py": textwrap.dedent(_CALENDAR),
        }
    )
    assert not project.errors(), project.errors()
    return ms.SemanticCatalog(project)


def test_cumulative_chain_and_snapshot(catalog: ms.SemanticCatalog) -> None:
    definition = catalog.metrics.get("sales.retail").details().definition
    payload = definition.to_dict()
    assert payload["schema"] == "marivo.semantic_definition/v1"
    assert payload["catalog_definition_fingerprint"] == catalog.definition_fingerprint
    node = payload["node"]
    assert isinstance(node, dict)
    assert node == {
        "kind": "cumulative",
        "base": {"schema": "marivo.semantic_ref/v1", "kind": "metric", "path": "sales.total"},
        "over": {
            "selection": "explicit",
            "ref": {
                "schema": "marivo.semantic_ref/v1",
                "kind": "time_dimension",
                "path": "sales.rows.day",
            },
        },
        "anchor": {
            "kind": "grain_to_date",
            "grain": {
                "kind": "semantic",
                "calendar": {
                    "schema": "marivo.semantic_ref/v1",
                    "kind": "period_calendar",
                    "path": "commerce.retail",
                },
                "level": "retail_quarter",
            },
        },
    }
    base = catalog.metrics.get("sales.total").details().definition
    assert base.node.kind == "aggregate"
    assert base.node.operation == "sum"
    leaf = catalog.require(base.node.target).details()
    assert isinstance(leaf, ms.MeasureDetails)
    assert leaf.definition.node.kind == "expression"
    assert leaf.definition.node.status == "supported"
    expression = leaf.definition.node.expression
    assert expression.kind == "cast" and expression.data_type == "float64"
    assert expression.operand.kind == "column" and expression.operand.name == "spend_cny"
    assert (
        leaf.definition.catalog_definition_fingerprint == definition.catalog_definition_fingerprint
    )
    assert "calendar:commerce.retail" not in json.dumps(payload)  # refs are typed objects
    assert "retail_quarter" in catalog.metrics.get("sales.retail").details().render()
    assert ".show()" in repr(definition) and "\n" not in repr(definition)
    with pytest.raises(FrozenInstanceError):
        definition.__setattr__("ref", base.ref)
    node["kind"] = "changed"
    assert definition.to_dict()["node"] != node


def test_anchor_variants_and_roles(catalog: ms.SemanticCatalog) -> None:
    nodes = {
        name: catalog.metrics.get("sales." + name).details().definition.to_dict()["node"]
        for name in [
            "all_time",
            "mtd",
            "rolling",
            "ratio",
            "repeated",
            "weighted",
            "p95",
            "count",
            "filtered",
        ]
    }
    assert nodes["all_time"] == {
        "kind": "cumulative",
        "base": {"schema": "marivo.semantic_ref/v1", "kind": "metric", "path": "sales.total"},
        "over": {"selection": "default", "resolution": "context_required"},
        "anchor": {"kind": "all_history"},
    }
    assert '"unit": "month"' in json.dumps(nodes["mtd"])
    assert '"count": 3, "unit": "day"' in json.dumps(nodes["rolling"])
    ratio = catalog.metrics.get("sales.ratio").details().definition.node
    assert ratio.kind == "ratio"
    assert ratio.numerator.path == "sales.total" and ratio.denominator.path == "sales.count"
    linear = catalog.metrics.get("sales.repeated").details().definition.node
    assert linear.kind == "linear"
    assert [(sign, ref.path) for sign, ref in linear.terms] == [
        ("+", "sales.total"),
        ("+", "sales.total"),
        ("-", "sales.count"),
    ]
    weighted = catalog.metrics.get("sales.weighted").details().definition.node
    assert weighted.kind == "weighted_mean"
    assert weighted.value.path == "sales.rows.spend" and weighted.weight.path == "sales.rows.weight"
    assert '"q": 0.95' in json.dumps(nodes["p95"])
    assert '"operator": "in", "values": ["east", "west"]' in json.dumps(nodes["count"])
    filtered = catalog.metrics.get("sales.filtered").details().definition.node
    assert filtered.kind == "aggregate"
    assert filtered.filter == ((ms.ref.dimension("sales.rows.region"), True),)
    assert "target_kind" in json.dumps(nodes["count"])
    tree = catalog.metrics.get("sales.repeated").details().render(max_output_bytes=None)
    assert "+term0" in tree and "+term1" in tree and "-term2" in tree


def test_expression_disclosure_and_time_rules(catalog: ms.SemanticCatalog) -> None:
    redacted = catalog.dimensions.get("sales.rows.redacted").details().definition
    text = json.dumps(redacted.to_dict()) + redacted.render() + repr(redacted.node)
    assert "private-token-987" not in text and "73921" not in text
    assert "redacted" in text and "ifelse" in text
    unsupported = catalog.dimensions.get("sales.rows.unsupported").details().definition
    assert unsupported.node.kind == "expression" and unsupported.node.status == "unsupported"
    assert unsupported.source_location.line > 0
    bound = catalog.measures.get("sales.rows.bound").details().definition
    assert "1234567" not in json.dumps(bound.to_dict())
    assert "sales.rows.spend" in json.dumps(bound.to_dict())
    state = catalog.measures.get("sales.rows.state").details().definition.temporal
    assert state.declared is not None and state.declared.fold.q == 0.95
    inherited = catalog.metrics.get("sales.state_total").details().definition.temporal
    assert inherited.source == "measure" and inherited.declared is None
    assert inherited.effective is not None and inherited.effective.fold.q == 0.95
    override = catalog.metrics.get("sales.state_override").details().definition.temporal
    assert override.source == "metric_override" and override.override is not None
    assert override.effective is not None and override.effective.fold.kind == "last"
    assert (
        catalog.metrics.get("sales.total").details().definition.temporal.source == "not_applicable"
    )


def test_loaded_reads_do_not_execute(
    catalog: ms.SemanticCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ibis

    import marivo.analysis as mv
    from marivo.datasource.runtime import DatasourceConnectionService
    from marivo.semantic import _expression_binding

    definitions: list[SemanticDefinition] = []
    for collection in (
        catalog.metrics,
        catalog.measures,
        catalog.dimensions,
        catalog.time_dimensions,
    ):
        for entry in collection:
            details = entry.details()
            assert isinstance(
                details,
                (
                    ms.SimpleMetricDetails,
                    ms.DerivedMetricDetails,
                    ms.MeasureDetails,
                    ms.DimensionDetails,
                    ms.TimeDimensionDetails,
                ),
            )
            definitions.append(details.definition)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("definition reading attempted execution or IO")

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
        for definition in definitions:
            json.dumps(definition.to_dict(), allow_nan=False)
            definition.render()
        for collection in (
            catalog.metrics,
            catalog.measures,
            catalog.dimensions,
            catalog.time_dimensions,
        ):
            for entry in collection:
                detail = entry.details()
                assert isinstance(
                    detail,
                    (
                        ms.SimpleMetricDetails,
                        ms.DerivedMetricDetails,
                        ms.MeasureDetails,
                        ms.DimensionDetails,
                        ms.TimeDimensionDetails,
                    ),
                )
                assert (
                    detail.definition.catalog_definition_fingerprint
                    == catalog.definition_fingerprint
                )


@pytest.mark.parametrize(
    ("name", "status"),
    [
        ("total", "not_applicable"),
        ("custom", "not_applicable"),
        ("repeated", "not_applicable"),
        ("gross_profit", "not_applicable"),
        ("nested_profit", "not_applicable"),
        ("ratio", "component_defined"),
        ("all_time", "component_defined"),
        ("mtd", "component_defined"),
        ("state_linear", "component_defined"),
        ("ratio_linear", "component_defined"),
        ("cumulative_linear", "component_defined"),
        ("percentile_linear", "component_defined"),
    ],
)
def test_temporal_composition_classification(
    catalog: ms.SemanticCatalog, name: str, status: str
) -> None:
    definition = catalog.metrics.get(f"sales.{name}").details().definition
    assert definition.to_dict()["temporal"] == {
        "declared": {"status": "not_declared"},
        "override": {"status": "not_declared"},
        "effective": {"status": status},
    }
    assert definition.temporal.source == status
    assert definition.temporal.effective is None
    assert status in definition.render()
    assert definition.catalog_definition_fingerprint == catalog.definition_fingerprint


def test_temporal_description_preserves_component_rules(catalog: ms.SemanticCatalog) -> None:
    definition = catalog.metrics.get("sales.state_linear").details().definition
    assert definition.node.kind == "linear"
    components: list[SemanticDefinition] = []
    for _, ref in definition.node.terms:
        details = catalog.require(ref).details()
        assert isinstance(details, ms.SimpleMetricDetails)
        components.append(details.definition)
    for component, source, fold in zip(
        components,
        ("measure", "metric_override"),
        ({"kind": "percentile", "q": 0.95}, {"kind": "last"}),
        strict=True,
    ):
        payload = component.to_dict()["temporal"]
        assert isinstance(payload, dict)
        assert payload["effective"] == {
            "status": "resolved",
            "source": source,
            "over": {
                "schema": "marivo.semantic_ref/v1",
                "kind": "time_dimension",
                "path": "sales.rows.day",
            },
            "fold": fold,
        }


@pytest.mark.parametrize("metric", ["sales.nested_profit", "sales.ratio", "sales.all_time"])
def test_missing_temporal_dependency_fails_closed(catalog: ms.SemanticCatalog, metric: str) -> None:
    from marivo.semantic._definition_projection import temporal_rules

    metrics = dict(catalog._reg.metrics)
    del metrics["sales.total"]
    registry = replace(catalog._reg, metrics=metrics)
    with pytest.raises(ms.SemanticDefinitionReadError, match="cannot be described safely") as exc:
        temporal_rules(registry.metrics[metric], registry)
    assert exc.value.received == "missing temporal component metric:sales.total"
    assert exc.value.expected and exc.value.location and exc.value.repair


def test_temporal_classification_checks_leaf_rules(catalog: ms.SemanticCatalog) -> None:
    from marivo.semantic._definition_projection import temporal_rules

    dimensions = dict(catalog._reg.dimensions)
    del dimensions["sales.rows.day"]
    registry = replace(catalog._reg, dimensions=dimensions)
    with pytest.raises(ms.SemanticDefinitionReadError) as exc:
        temporal_rules(registry.metrics["sales.state_linear"], registry)
    assert "unresolved status time dimension" in exc.value.received


def test_temporal_classification_rejects_invalid_override(catalog: ms.SemanticCatalog) -> None:
    from marivo.semantic._definition_projection import temporal_rules

    state = catalog._reg.measures["sales.rows.state"]
    measures = dict(catalog._reg.measures)
    measures[state.semantic_id] = replace(state, additivity="additive")
    registry = replace(catalog._reg, measures=measures)
    with pytest.raises(ms.SemanticDefinitionReadError) as exc:
        temporal_rules(registry.metrics["sales.state_linear"], registry)
    assert exc.value.received == "fold override has no applicable status-time contract"


def test_temporal_classification_rejects_cycles_and_missing_leaves(
    catalog: ms.SemanticCatalog,
) -> None:
    from marivo.semantic._definition_projection import temporal_rules
    from marivo.semantic.ir import RatioComposition

    metrics = dict(catalog._reg.metrics)
    metrics["sales.ratio"] = replace(
        metrics["sales.ratio"],
        composition=RatioComposition(numerator="sales.ratio", denominator="sales.total"),
    )
    registry = replace(catalog._reg, metrics=metrics)
    with pytest.raises(ms.SemanticDefinitionReadError) as exc:
        temporal_rules(registry.metrics["sales.ratio"], registry)
    assert "cyclic metric dependency" in exc.value.received

    measures = dict(catalog._reg.measures)
    del measures["sales.rows.spend"]
    registry = replace(catalog._reg, measures=measures)
    with pytest.raises(ms.SemanticDefinitionReadError) as exc:
        temporal_rules(registry.metrics["sales.nested_profit"], registry)
    assert "missing temporal aggregate target" in exc.value.received


def test_temporal_read_preserves_calculation_graph(catalog: ms.SemanticCatalog) -> None:
    from marivo.semantic.metric_graph_lowering import lower_catalog_metric

    fingerprint = catalog.definition_fingerprint
    for name in ("gross_profit", "state_linear", "ratio_linear", "cumulative_linear"):
        metric_id = f"sales.{name}"
        before = lower_catalog_metric(catalog._reg, metric_id)
        catalog.metrics.get(metric_id).details().definition.to_dict()
        assert lower_catalog_metric(catalog._reg, metric_id) == before
    assert catalog.definition_fingerprint == fingerprint


@pytest.mark.parametrize("shape", ["depth", "width"])
def test_temporal_classification_is_bounded(catalog: ms.SemanticCatalog, shape: str) -> None:
    from marivo.semantic._definition_projection import temporal_rules
    from marivo.semantic.ir import LinearComposition, LinearTerm, RatioComposition
    from marivo.semantic.metric_graph import MAX_EXPRESSION_DEPTH, MAX_EXPRESSION_OCCURRENCES

    metrics = dict(catalog._reg.metrics)
    root = metrics["sales.ratio"]
    if shape == "depth":
        for index in range(MAX_EXPRESSION_DEPTH):
            root = replace(
                root,
                semantic_id=f"sales.nested_{index}",
                composition=RatioComposition(numerator=root.semantic_id, denominator="sales.total"),
            )
            metrics[root.semantic_id] = root
    else:
        root = replace(
            root,
            composition=LinearComposition(
                terms=tuple(
                    LinearTerm(sign="+", metric="sales.total")
                    for _ in range(MAX_EXPRESSION_OCCURRENCES)
                )
            ),
        )
    registry = replace(catalog._reg, metrics=metrics)
    with pytest.raises(ms.SemanticDefinitionReadError) as exc:
        temporal_rules(root, registry)
    assert "depth or occurrence limits" in exc.value.received


def test_definition_help(catalog: ms.SemanticCatalog, capsys: pytest.CaptureFixture[str]) -> None:
    marivo_help("semantic.SemanticDefinition")
    text = capsys.readouterr().out
    assert "to_dict" in text and "catalog_definition_fingerprint" in text
    assert "component_defined" in text and "not_applicable" in text
    assert "not the effective fold" in text
    marivo_help("semantic.MeasureDetails")
    assert "definition" in capsys.readouterr().out
    assert isinstance(catalog.metrics.get("sales.total").details().definition, SemanticDefinition)
    definition = catalog.metrics.get("sales.total").details().definition
    error = ms.SemanticDefinitionReadError(
        ref=definition.ref.key,
        location=definition.source_location,
        received="unknown metric composition",
    )
    for target in (
        "semantic.SemanticDefinitionReadError",
        ms.SemanticDefinitionReadError,
        error,
    ):
        marivo_help(target)
        rendered = capsys.readouterr().out
        assert "Semantic error" in rendered
        assert "public properties" not in rendered
    marivo_help(error)
    rendered = capsys.readouterr().out
    assert "unknown metric composition" in rendered
    assert "reauthor" in rendered
    assert "reported location" in rendered


def test_nonfinite_filter_fails_with_safe_typed_error(catalog: ms.SemanticCatalog) -> None:
    definition = catalog.metrics.get("sales.filtered").details().definition
    assert definition.node.kind == "aggregate"
    bad = replace(
        definition,
        node=replace(
            definition.node, filter=((ms.ref.dimension("sales.rows.region"), float("nan")),)
        ),
    )
    with pytest.raises(ms.SemanticDefinitionReadError) as exc:
        bad.to_dict()
    assert exc.value.expected and exc.value.received and exc.value.repair
    assert exc.value.semantic_refs == (definition.ref.key,)
    assert exc.value.repair.kind == "reauthor"
    assert "reload" in exc.value.repair.action.lower()


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), ("east", float("nan"))],
)
def test_nonfinite_filter_is_rejected_during_authoring(
    value: str | int | float | bool | tuple[str | int | float | bool, ...],
) -> None:
    from marivo.semantic.errors import SemanticDecoratorError

    with pytest.raises(SemanticDecoratorError) as exc:
        ms.where(region=value)
    assert exc.value.kind == "invalid_filter"
    assert exc.value.expected and exc.value.received
    assert exc.value.repair is not None and exc.value.repair.kind == "reauthor"


def test_expression_operator_tokens_are_syntax_faithful() -> None:
    import ast

    from marivo.semantic._definition_expression import describe_expression

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


def test_details_full_render_propagates_definition_budget(
    semantic_project_factory: Factory,
) -> None:
    columns = [f"t.column_{index}" for index in range(128)]
    while len(columns) > 1:
        columns = [
            f"({columns[index]} + {columns[index + 1]})" for index in range(0, len(columns), 2)
        ]
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\nms.domain(name='sales', owner='Owner')\n"
            ),
            "sales/models.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "rows = ms.entity(name='rows', datasource=ms.ref.datasource('warehouse'), "
                "source=md.table('rows'))\n"
                "@ms.dimension(name='wide', entity=rows)\n"
                "def wide(t):\n"
                f"    return {columns[0]}\n"
            ),
        }
    )
    assert not project.errors(), project.errors()
    details = ms.SemanticCatalog(project).dimensions.get("sales.rows.wide").details()
    direct = details.definition.render(max_output_bytes=None)
    rendered = details.render(max_output_bytes=None)
    assert len(direct.encode("utf-8")) > 8192
    assert "column_127" in direct and "column_127" in rendered
    assert "output truncated" not in rendered
    bounded = details.render()
    assert len(bounded.encode("utf-8")) <= 8192
    assert "pass max_output_bytes=None for full output" in bounded


@pytest.mark.parametrize(
    ("expression", "kind"),
    [
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
    ],
)
def test_supported_expression_matrix(
    semantic_project_factory: Factory, expression: str, kind: str
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Owner')\n",
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nrows=ms.entity(name='rows', datasource=ms.ref.datasource('warehouse'), source=md.table('rows'))\n@ms.dimension(entity=rows)\ndef value(t):\n    return "
            + expression
            + "\n",
        }
    )
    definition = ms.SemanticCatalog(project).dimensions.get("sales.rows.value").details().definition
    assert definition.node.kind == "expression" and definition.node.status == "supported"
    assert definition.node.expression.kind == kind
    payload = definition.to_dict()["node"]
    assert isinstance(payload, dict)
    display = payload["display"]
    assert isinstance(display, dict)
    assert display["form"] == "normalized_ibis"
    assert isinstance(display["text"], str)


def test_projection_does_not_change_fingerprint(
    catalog: ms.SemanticCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.semantic import _expression_binding
    from marivo.semantic.definition import _UnsupportedExpression

    def unavailable(*args: object, **kwargs: object) -> _UnsupportedExpression:
        return _UnsupportedExpression("description_unavailable")

    monkeypatch.setattr(_expression_binding, "describe_expression", unavailable)
    current = ms.load(workspace_dir=catalog.semantic_root.parent.parent)
    assert current.definition_fingerprint == catalog.definition_fingerprint
    current_node = current.measures.get("sales.rows.spend").details().definition.node
    assert current_node.kind == "expression" and current_node.status == "unsupported"
    original = catalog.measures.get("sales.rows.spend").details().definition.node
    assert original.kind == "expression" and original.status == "supported"


def test_description_limits_and_external_names() -> None:
    import ast

    from marivo.semantic._definition_expression import describe_expression

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


def test_unknown_loaded_composition_is_a_read_error(catalog: ms.SemanticCatalog) -> None:
    from marivo.semantic._definition_projection import metric_node

    registry = catalog._reg
    malformed = replace(registry.metrics["sales.total"])
    object.__setattr__(malformed, "composition", object())
    with pytest.raises(ms.SemanticDefinitionReadError) as exc:
        metric_node(malformed, registry)
    assert exc.value.received == "unknown metric composition"
