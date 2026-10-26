"""Public source conditions checked against independent literal tuple oracles."""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.semantic.errors import SemanticError
from marivo.semantic.reader import SemanticProject

_MODEL = """import marivo.datasource as md
import marivo.semantic as ms
warehouse = ms.ref.datasource("warehouse")
orders = ms.entity(name="order", datasource=warehouse, source=md.table("orders"), primary_key=["id"])
oid = ms.dimension_column(name="id", entity=orders, column="id")
status = ms.dimension_column(name="status", entity=orders, column="status")
region = ms.dimension_column(name="region", entity=orders, column="region")
channel = ms.dimension_column(name="channel", entity=orders, column="channel")
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="USD")
divisor = ms.measure_column(name="divisor", entity=orders, column="divisor", additivity=ms.non_additive())
refunds = ms.entity(name="refund", datasource=warehouse, source=md.table("refunds"), primary_key=["id"])
refund_amount = ms.measure_column(name="amount", entity=refunds, column="amount", additivity=ms.additive_all(), unit="USD")
tenants = ms.entity(name="tenant_order", datasource=warehouse, source=md.table("tenant_orders"), primary_key=["tenant", "id"])
tenant_amount = ms.measure_column(name="amount", entity=tenants, column="amount", additivity=ms.additive_all())
"""


@pytest.fixture
def predicate_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> mv.Session:
    database = tmp_path / "predicates.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("SET threads=1")
        connection.execute(
            "CREATE TABLE orders(id VARCHAR, status VARCHAR, amount BIGINT, "
            "region VARCHAR, channel VARCHAR, divisor BIGINT)"
        )
        connection.execute(
            "INSERT INTO orders VALUES "
            "('O1','paid',60,'east','web',1),"
            "('O2','pending',40,'west',NULL,1),"
            "('O3',NULL,10,'east',NULL,0),"
            "('O4','paid',20,'west','web',1),"
            "('O5','cancelled',30,'other','store',1),"
            "('O6',NULL,5,'other',NULL,1)"
        )
        connection.execute("CREATE TABLE refunds(id VARCHAR, amount BIGINT)")
        connection.execute("INSERT INTO refunds VALUES ('R1',2),('R2',3)")
        connection.execute("CREATE TABLE tenant_orders(tenant VARCHAR, id VARCHAR, amount BIGINT)")
        connection.execute("INSERT INTO tenant_orders VALUES ('A','1',7),('B','1',11)")
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database)!r})\n"
            ),
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics", default=True)\n',
            "sales/objects.py": _MODEL,
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("predicates", timezone="UTC")


def _preferred(session: mv.Session) -> mv.Population:
    entity = session.catalog.entities.get("sales.order")
    return mv.coordinates(by=(mv.identity(entity),), keys=((("O1",),), (("O2",),), (("O3",),)))


def _condition(session: mv.Session, form: str) -> Deferred:
    field = session.catalog.fields.get("sales.order.status") == "paid"
    member = mv.in_population(_preferred(session))
    if form == "and":
        return field & member
    if form == "or":
        return field | member
    if form == "not_or":
        return ~(field | member)
    if form == "not_and":
        return ~(field & member)
    return field & ~member


@pytest.mark.parametrize(
    "form,identities,total",
    (
        ("and", ["O1"], 60),
        ("or", ["O1", "O2", "O3", "O4"], 130),
        ("not_or", ["O5"], 30),
        ("not_and", ["O2", "O4", "O5", "O6"], 95),
        ("and_not", ["O4"], 20),
    ),
)
def test_mixed_condition_public_consumers(
    predicate_session: mv.Session, form: str, identities: list[str], total: int
) -> None:
    """U01/U02: six rows include every nullable field/member truth-table case."""
    session = predicate_session
    amount = session.catalog.fields.get("sales.order.amount")
    entity = session.catalog.entities.get("sales.order")
    condition = _condition(session, form)
    assert isinstance(condition, Deferred)
    observed = session.observe({"amount": amount.sum()}, where=condition).compute().to_pandas()
    intrinsic = session.observe({"amount": amount.sum().where(condition)}).compute().to_pandas()
    assert observed["amount"].tolist() == [total]
    assert intrinsic["amount"].tolist() == [total]
    selected = session.frame(session.members(entity, where=condition)).compute().to_pandas()
    assert selected.iloc[:, 0].tolist() == identities


def test_reverse_nested_and_repeated_where(predicate_session: mv.Session) -> None:
    """U01: operand order and extraction preserve boolean meaning."""
    session = predicate_session
    amount = session.catalog.fields.get("sales.order.amount")
    paid = session.catalog.fields.get("sales.order.status") == "paid"
    member = mv.in_population(_preferred(session))
    values = (
        session.observe(
            {
                "reverse": amount.sum().where(member | paid),
                "nested": amount.sum().where(~(~member & ~paid)),
                "repeated": amount.sum().where(member).where(paid),
                "conjoined": amount.sum().where(member & paid),
            }
        )
        .compute()
        .to_pandas()
    )
    assert values.iloc[0].tolist() == [130, 130, 60, 60]


def test_correlated_null_coordinates_and_composite_identity(predicate_session: mv.Session) -> None:
    """U02: tuple matching preserves correlation, null categories and tenant identity."""
    session = predicate_session
    region = session.catalog.fields.get("sales.order.region")
    channel = session.catalog.fields.get("sales.order.channel")
    amount = session.catalog.fields.get("sales.order.amount")
    joint = mv.coordinates(by=(region, channel), keys=(("east", "web"), ("west", None)))
    selected = session.observe({"amount": amount.sum()}, where=mv.in_population(joint)).compute()
    assert selected.to_pandas()["amount"].tolist() == [100]
    all_keys = mv.coordinates(
        by=(region, channel),
        keys=(("east", "web"), ("east", None), ("west", "web"), ("west", None)),
    )
    keys = session.frame(all_keys.where(~mv.in_population(joint))).compute().to_pandas()
    assert keys.iloc[:, 0].tolist() == ["east", "west"]
    assert keys.iloc[:, 1].isna().tolist() == [True, False]
    assert keys.iloc[1, 1] == "web"
    tenant = session.catalog.entities.get("sales.tenant_order")
    population = mv.coordinates(by=(mv.identity(tenant),), keys=((("A", "1"),),))
    tenant_amount = session.catalog.fields.get("sales.tenant_order.amount")
    result = session.observe({"amount": tenant_amount.sum()}, where=mv.in_population(population))
    assert result.compute().to_pandas()["amount"].tolist() == [7]
    with pytest.raises((AnalysisError, SemanticError), match="None"):
        mv.coordinates(by=(mv.identity(tenant),), keys=((("A", None),),))


def test_empty_unit_relative_complements_and_output_domain(predicate_session: mv.Session) -> None:
    """U03: conditions select existing rows; complete requested coordinates survive."""
    session = predicate_session
    amount = session.catalog.fields.get("sales.order.amount")
    region = session.catalog.fields.get("sales.order.region")
    empty = mv.coordinates(by=(region,), keys=())
    unit = mv.coordinates(by=(), keys=((),))
    empty_unit = mv.coordinates(by=(), keys=())
    result = (
        session.observe(
            {
                "none": amount.sum().where(mv.in_population(empty)),
                "zero": amount.sum().where(mv.in_population(empty)),
                "count": amount.count().where(mv.in_population(empty)),
                "unit": amount.sum().where(mv.in_population(unit)),
                "complement": amount.sum().where(~mv.in_population(empty_unit)),
            }
        )
        .compute()
        .to_pandas()
    )
    assert result["none"].tolist() == [0]
    assert result.iloc[0, 1:].tolist() == [0, 0, 165, 165]
    assert session.frame(empty.where(~mv.in_population(empty))).compute().to_pandas().empty
    output = mv.coordinates(by=(region,), keys=(("east",), ("missing",)))
    retained = session.observe(
        {"amount": amount.sum()}, by=output, where=_condition(session, "and")
    )
    values = retained.compute().to_pandas()
    assert values.iloc[:, 0].tolist() == ["east", "missing"]
    assert values["amount"].iloc[0] == 60
    assert values["amount"].tolist() == [60, 0]


@pytest.mark.parametrize("boolean_operator", ("and", "or"))
def test_boolean_membership_does_not_shield_strict_checks(
    predicate_session: mv.Session, boolean_operator: str
) -> None:
    """U04: invalid field functions remain checked across membership booleans."""
    session = predicate_session
    amount = session.catalog.fields.get("sales.order.amount")
    divisor = session.catalog.fields.get("sales.order.divisor")
    entity = session.catalog.entities.get("sales.order")
    risky = divisor.log() > 0
    population = (
        mv.coordinates(by=(mv.identity(entity),), keys=((("O1",),),))
        if boolean_operator == "and"
        else _preferred(session)
    )
    member = mv.in_population(population)
    condition = member & risky if boolean_operator == "and" else member | risky
    with pytest.raises(AnalysisError, match="valid finite numeric function inputs"):
        session.observe({"amount": amount.sum()}, where=condition).compute()
    with pytest.raises(AnalysisError, match="strict arithmetic checks"):
        session.frame(session.members(entity, where=condition)).compute()


def test_population_count_is_not_reselected(predicate_session: mv.Session) -> None:
    """U05: one missing literal identity belongs to the independent denominator."""
    session = predicate_session
    entity = session.catalog.entities.get("sales.order")
    amount = session.catalog.fields.get("sales.order.amount")
    population = mv.coordinates(
        by=(mv.identity(entity),), keys=tuple(((value,),) for value in ("O1", "O2", "O3", "O999"))
    )
    member = mv.in_population(population)
    fact_count = entity.count().where(member)
    condition = (session.catalog.fields.get("sales.order.status") == "paid") & member
    result = (
        session.observe(
            {"population": population.count(), "facts": fact_count, "amount": amount.sum()},
            where=condition,
        )
        .compute()
        .to_pandas()
    )
    assert result.iloc[0].tolist() == [4, 1, 60]
    unconditioned = session.observe({"facts": fact_count}).compute().to_pandas()
    assert unconditioned["facts"].tolist() == [3]
    with pytest.raises((AnalysisError, SemanticError)):
        session.observe({"population": population.count()}, where=condition)


def test_independent_roots_and_common_weighted_pairs(predicate_session: mv.Session) -> None:
    """U05: each root reduces its own rows and value/weight selection stays paired."""
    session = predicate_session
    amount = session.catalog.fields.get("sales.order.amount")
    weight = session.catalog.fields.get("sales.order.divisor")
    refund = session.catalog.fields.get("sales.refund.amount")
    unit = mv.coordinates(by=(), keys=((),))
    independent = (
        session.observe({"net": amount.sum() - refund.sum()}, where=mv.in_population(unit) | False)
        .compute()
        .to_pandas()
    )
    assert independent["net"].tolist() == [160]
    paired = (
        session.observe(
            {"weighted": amount.weighted_mean(weight=weight)}, where=_condition(session, "or")
        )
        .compute()
        .to_pandas()
    )
    assert paired["weighted"].tolist() == [40]


def test_pure_field_branch_demand_and_constant_producer_failure(
    predicate_session: mv.Session,
) -> None:
    """U04/U06: pure field branches retain protection; constant members retain producers."""
    session = predicate_session
    amount = session.catalog.fields.get("sales.order.amount")
    divisor = session.catalog.fields.get("sales.order.divisor")
    risky = amount / divisor
    guarded = (divisor != 0).ifelse(risky, 0) > 0
    result = (
        session.observe(
            {"amount": amount.sum()}, where=guarded & mv.in_population(_preferred(session))
        )
        .compute()
        .to_pandas()
    )
    assert result["amount"].tolist() == [100]
    entity = session.catalog.entities.get("sales.order")
    source = session.read(session.members(entity), {"amount": amount})
    invalid = source.select({"invalid": source["amount"] * 1e308})
    empty = invalid.top(0, by=invalid["invalid"])
    producer = session.population_from(empty, on=((empty.axes[0], entity),))
    for condition in (mv.in_population(producer) | True, mv.in_population(producer) & False):
        with pytest.raises(AnalysisError, match="finite numeric output"):
            session.observe({"amount": amount.sum()}, where=condition).compute()


def test_native_grammar_and_consumer_repairs(predicate_session: mv.Session) -> None:
    """U01/U04: invalid member methods and unselected leaves cannot bypass admission."""
    session = predicate_session
    amount = session.catalog.fields.get("sales.order.amount")
    paid = session.catalog.fields.get("sales.order.status") == "paid"
    population = _preferred(session)
    member = mv.in_population(population)
    invalid_conditions = (member == True, member.isnull(), member.cast("boolean"), member + 1)  # noqa: E712
    for condition in invalid_conditions:
        with pytest.raises((AnalysisError, SemanticError)):
            session.observe({"amount": amount.sum()}, where=condition)
    with pytest.raises((AnalysisError, SemanticError)) as captured:
        population.where(member | paid)
    assert "members" in str(captured.value)
    with pytest.raises((AnalysisError, SemanticError)):
        session.observe({"amount": amount.sum(where=member)})
    foreign = session.catalog.fields.get("sales.refund.amount")
    with pytest.raises((AnalysisError, SemanticError)):
        session.observe({"amount": amount.sum()}, where=member | ((foreign > 0) & False))
    frame = session.observe({"amount": amount.sum()})
    with pytest.raises(AnalysisError):
        frame.where(paid)


def test_fixed_and_pending_frame_keys_in_nested_conditions(predicate_session: mv.Session) -> None:
    """U06: public population_from retains exact fixed input and pending selection."""
    session = predicate_session
    entity = session.catalog.entities.get("sales.order")
    amount = session.catalog.fields.get("sales.order.amount")
    status = session.catalog.fields.get("sales.order.status")
    source = session.read(session.members(entity), {"amount": amount})
    chosen = source.where(source["amount"] > 50)
    fixed = chosen.persist()
    fixed_population = session.population_from(fixed, on=((fixed.axes[0], entity),))
    pending_population = session.population_from(chosen, on=((chosen.axes[0], entity),))
    fixed_condition = ~((~mv.in_population(fixed_population)) & (status != "never"))
    pending_condition = ~((~mv.in_population(pending_population)) & (status != "never"))
    with duckdb.connect(str(session.project_root / "predicates.duckdb")) as connection:
        connection.execute("UPDATE orders SET amount=100 WHERE id='O2'")
    result = (
        session.observe(
            {
                "fixed": amount.sum().where(fixed_condition),
                "pending": amount.sum().where(pending_condition),
            }
        )
        .compute()
        .to_pandas()
    )
    assert result.iloc[0].tolist() == [60, 160]
