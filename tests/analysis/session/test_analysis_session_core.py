"""Public Session ownership and external semantic layer wiring."""

import textwrap
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
from marivo._definition.expressions import DeclaredMetricRef
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic.catalog import SemanticCatalog
from tests.shared_fixtures import terminal_runs


def test_session_public_fields_are_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("immutable")
    for name in ("id", "name", "created_at"):
        with pytest.raises(AttributeError):
            setattr(session, name, "other")


def test_session_exposes_catalog_property(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("catalog")
    assert isinstance(session.catalog, SemanticCatalog)
    assert session.catalog.workspace_dir == tmp_path


def test_session_catalog_loads_external_semantic_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import textwrap

    import marivo.analysis as mv

    project_root = tmp_path / "project"
    external_models = tmp_path / "external" / "models"
    project_root.mkdir()
    (project_root / "marivo.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "demo"

            [semantic]
            layer_paths = ["../external/models"]
            """
        ),
        encoding="utf-8",
    )
    for models_root, datasource, domain, entity, metric in (
        (project_root / "models", "local_warehouse", "sales", "orders", "revenue"),
        (external_models, "external_warehouse", "finance", "refunds", "refunds_total"),
    ):
        datasource_dir = models_root / "datasources"
        semantic_dir = models_root / "semantic" / domain
        datasource_dir.mkdir(parents=True, exist_ok=True)
        semantic_dir.mkdir(parents=True, exist_ok=True)
        (datasource_dir / f"{datasource}.py").write_text(
            f"import marivo.datasource as md\nmd.duckdb(name={datasource!r}, path=':memory:')\n",
            encoding="utf-8",
        )
        (semantic_dir / "_domain.py").write_text(
            f"import marivo.semantic as ms\nms.domain(name={domain!r}, owner='Mina Zhang')\n",
            encoding="utf-8",
        )
        (semantic_dir / "objects.py").write_text(
            textwrap.dedent(
                f"""
                import marivo.datasource as md
                import marivo.semantic as ms

                source = ms.ref.datasource("{datasource}")
                rows = ms.entity(name={entity!r}, datasource=source, source=md.table({entity!r}, columns={{"id": "id", "amount": "amount"}}), primary_key=["id"])

                amount_ref = ms.measure_column(name="amount", entity=rows, column="amount", additivity=ms.additive_all())
                # Named expressions
                amount = amount_ref
                @ms.metric()
                def {metric}():
                    return amount.sum()
                """
            ),
            encoding="utf-8",
        )

    monkeypatch.chdir(project_root)

    session = mv.session.get_or_create(name="external_layer_session")

    refunds = consume_metric(session.catalog.get("metric:finance.refunds_total"))
    assert isinstance(refunds.node, DeclaredMetricRef)
    assert refunds.node.ref.ref.path == "finance.refunds_total"


def test_session_observe_uses_external_layer_datasource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis as mv

    project_root = tmp_path / "project"
    external_models = tmp_path / "external" / "models"
    db_path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE refunds (id BIGINT, amount DOUBLE, occurred_at DATE)")
    con.execute(
        "INSERT INTO refunds VALUES (1, 100.0, DATE '2026-07-01'), (2, 50.0, DATE '2026-07-01')"
    )
    con.close()
    project_root.mkdir()
    (project_root / "marivo.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "demo"

            [semantic]
            layer_paths = ["../external/models"]
            """
        ),
        encoding="utf-8",
    )
    datasource_dir = external_models / "datasources"
    semantic_dir = external_models / "semantic" / "finance"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    semantic_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(db_path)!r})\n",
        encoding="utf-8",
    )
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='finance', owner='Mina Zhang')\n",
        encoding="utf-8",
    )
    (semantic_dir / "objects.py").write_text(
        textwrap.dedent(
            """
            import marivo.datasource as md
            import marivo.semantic as ms

            source = ms.ref.datasource("warehouse")
            rows = ms.entity(name="refunds", datasource=source, source=md.table("refunds", columns={"id": "id", "amount": "amount", "occurred_at": "occurred_at"}), primary_key=["id"])

            amount = ms.measure_column(name="amount", entity=rows, column="amount", additivity=ms.additive_all())
            instant = ms.time_dimension_column(name="instant", entity=rows, column="occurred_at", parse=None, is_default=True)
            amount_value = amount
            @ms.metric()
            def refunds_total():
                return amount_value.sum()
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project_root)

    session = mv.session.get_or_create(name="external_layer_observe", timezone="UTC")
    metric = session.catalog.get("metric:finance.refunds_total")
    frame = session.observe({"refunds": metric}).compute()

    assert frame.to_pandas()["refunds"].tolist() == [150.0]
    assert terminal_runs(session) == ()


def _write_object_model(
    root: Path,
    *,
    multiplier: int = 1,
    classification: str = "region",
    numeric_column: str = "amount",
) -> None:
    """A two-row model with independently known arithmetic and classifications."""
    source = root / "models" / "datasources"
    semantic = root / "models" / "semantic" / "sales"
    source.mkdir(parents=True, exist_ok=True)
    semantic.mkdir(parents=True, exist_ok=True)
    database = root / "objects.duckdb"
    if not database.exists():
        with duckdb.connect(str(database)) as connection:
            connection.execute(
                "CREATE TABLE facts(id BIGINT, amount BIGINT, cost BIGINT, region VARCHAR, alternate VARCHAR, doubled BIGINT)"
            )
            connection.execute(
                "INSERT INTO facts VALUES (1,10,3,'east','west',20),(2,20,8,'west','east',40)"
            )
    (root / "marivo.toml").write_text('[project]\nname = "object-consumption"\n')
    (source / "warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n"
    )
    (semantic / "model.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table('facts'), primary_key=['id'])\n"
        f"region = ms.dimension_column(name='region', entity=facts, column={classification!r})\n"
        f"amount = ms.measure_column(name='amount', entity=facts, column={numeric_column!r}, additivity=ms.additive_all())\n"
        "cost = ms.measure_column(name='cost', entity=facts, column='cost', additivity=ms.additive_all())\n"
        "@ms.metric()\ndef revenue():\n"
        f"    return amount.sum() * {multiplier}\n"
        "@ms.metric()\ndef order_count():\n    return facts.count()\n"
        "@ms.metric()\ndef profit():\n    return (amount - cost).sum()\n"
        + "# scope revision "
        + "x" * multiplier
        + "\n"
    )


def test_named_objects_and_observe_containers_capture_independent_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.semantic as ms

    _write_object_model(tmp_path)
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("object-arithmetic")
    revenue = session.catalog.get("metric:sales.revenue")
    orders = session.catalog.get("metric:sales.order_count")
    profit = session.catalog.get("metric:sales.profit")
    assert (
        isinstance(revenue, ms.Metric)
        and isinstance(orders, ms.Metric)
        and isinstance(profit, ms.Metric)
    )
    metrics = [revenue, orders, profit]
    pending = session.observe(metrics)
    metrics[:] = [orders]
    assert pending.output_names == ("revenue", "order_count", "profit")
    assert pending.compute().to_pandas().iloc[0].tolist() == [30, 2, 19]
    assert session.observe((revenue, orders, profit)).compute().to_pandas().iloc[0].tolist() == [
        30,
        2,
        19,
    ]
    formulas = {"average": revenue / orders}
    average = session.observe(formulas)
    formulas["average"] = revenue * 0
    assert average.compute().to_pandas()["average"].tolist() == [15]
    assert session.observe(revenue).compute().to_pandas()["revenue"].tolist() == [30]
    from marivo.analysis.errors import AnalysisError

    for invalid in ([], (), {}, [revenue / orders], (revenue * 1,)):
        with pytest.raises(AnalysisError) as caught:
            session.observe(invalid)
        assert caught.value.expected and caught.value.received and caught.value.repair
    with pytest.raises(AnalysisError, match=r"metrics\[1\]"):
        session.observe([revenue, revenue / orders])


def test_receiving_session_rebinds_named_leaves_but_keeps_captured_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.semantic as ms

    _write_object_model(tmp_path)
    monkeypatch.chdir(tmp_path)
    first = mv.session.get_or_create("scope-one")
    old = first.catalog.get("metric:sales.revenue")
    assert isinstance(old, ms.Metric)
    captured = first.observe(old)
    old_definition = old.definition
    _write_object_model(tmp_path, multiplier=2)
    second = mv.session.get_or_create("scope-two")
    assert second.observe({"half": old / 2}).compute().to_pandas()["half"].tolist() == [30]
    assert captured.compute().to_pandas()["revenue"].tolist() == [30]
    assert old.definition == old_definition
    assert second.observe(old).compute().to_pandas()["revenue"].tolist() == [60]


def test_loaded_wrappers_keep_project_and_axes_do_not_gain_selection_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.semantic as ms
    from marivo.analysis.errors import AnalysisError
    from marivo.semantic.errors import SemanticError

    project_a, project_b = tmp_path / "a", tmp_path / "b"
    _write_object_model(project_a)
    _write_object_model(project_b)
    monkeypatch.chdir(project_a)
    first = mv.session.get_or_create("first")
    entity = first.catalog.get("entity:sales.facts")
    region = first.catalog.get("dimension:sales.facts.region")
    assert isinstance(entity, ms.Entity) and isinstance(region, ms.Dimension)
    grouping = mv.identity(entity)
    literal = mv.coordinates(by=(grouping,), keys=(((1,),),))
    fields = mv.coordinates(by=(region,), keys=(("east",),))
    people = first.members(entity)
    other = mv.session.get_or_create("second")
    # axes carries only rules; the selected source domain remains owned by first.
    assert other.observe(
        {"size": mv.coordinates(by=people.axes, keys=(((1,),),)).count()}
    ).compute().to_pandas()["size"].tolist() == [1]
    with pytest.raises(AnalysisError):
        other.observe({"size": people.count()})
    monkeypatch.chdir(project_b)
    foreign = mv.session.get_or_create("foreign")
    metric = foreign.catalog.get("metric:sales.revenue")
    assert isinstance(metric, ms.Metric)
    for by in ((grouping,), fields, fields.axes, literal):
        with pytest.raises((SemanticError, AnalysisError)):
            foreign.observe(metric, by=by)
    for derived in (literal.count(), fields.count()):
        with pytest.raises(SemanticError):
            foreign.observe({"size": derived})
    with pytest.raises(SemanticError):
        foreign.observe(metric, where=mv.in_population(fields))
    foreign_frame = foreign.observe(metric)
    for by in ((grouping,), fields):
        with pytest.raises(AnalysisError):
            foreign_frame.attribute(by=by)


def test_attribute_resolves_each_endpoint_classification_without_rebinding_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.semantic as ms

    _write_object_model(tmp_path)
    with duckdb.connect(str(tmp_path / "objects.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET amount=CASE id WHEN 1 THEN 5 ELSE 15 END, doubled=CASE id WHEN 1 THEN 10 ELSE 30 END"
        )
    monkeypatch.chdir(tmp_path)
    first = mv.session.get_or_create("retained-scopes")
    revenue = first.catalog.get("metric:sales.revenue")
    assert isinstance(revenue, ms.Metric)
    old = first.observe(revenue)
    _write_object_model(tmp_path, classification="alternate", numeric_column="doubled")
    second = mv.session.get_or_create("retained-scopes")
    classification = second.catalog.get("dimension:sales.facts.region")
    assert isinstance(classification, ms.Dimension)
    current = second.observe(revenue)
    compared = current.compare({"reference": mv.baseline(old)})
    selected = compared.select({"change": compared["reference.absolute.revenue"]})
    report = selected.attribute(by=(classification,)).compute().to_pandas()
    rows = report.sort_values(report.columns[0]).reset_index(drop=True)
    assert rows.iloc[:, 0].tolist() == ["east", "west"]
    assert rows["detail.current.change"].tolist() == [30, 10]
    assert rows["detail.baseline.change"].tolist() == [5, 15]
    assert rows["contribution.change"].tolist() == [25, -5]
    assert old.compute().to_pandas()["revenue"].tolist() == [20]


def test_attribute_missing_classification_reports_the_retained_endpoint_before_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.semantic as ms
    from marivo.analysis.errors import AnalysisError
    from marivo.datasource.adapters import SourceSession

    _write_object_model(tmp_path)
    model = tmp_path / "models/semantic/sales/model.py"
    model.write_text(
        "\n".join(
            line for line in model.read_text().splitlines() if not line.startswith("region =")
        )
    )
    monkeypatch.chdir(tmp_path)
    first = mv.session.get_or_create("missing-classification")
    metric = first.catalog.get("metric:sales.revenue")
    assert isinstance(metric, ms.Metric)
    old = first.observe(metric)
    _write_object_model(tmp_path)
    second = mv.session.get_or_create("missing-classification")
    field = second.catalog.get("dimension:sales.facts.region")
    assert isinstance(field, ms.Dimension)
    current = second.observe(metric)
    compared = current.compare({"reference": mv.baseline(old)})
    selected = compared.select({"change": compared["reference.absolute.revenue"]})

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("attribute must reject missing classification before source reads")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    with pytest.raises(AnalysisError) as caught:
        selected.attribute(by=(field,))
    assert caught.value.received and "right endpoint" in caught.value.received
    assert "dimension:sales.facts.region" in caught.value.received


def test_literal_identity_keys_cannot_be_reinterpreted_after_primary_key_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.semantic as ms
    from marivo.analysis.errors import AnalysisError

    _write_object_model(tmp_path)
    monkeypatch.chdir(tmp_path)
    first = mv.session.get_or_create("identity-one")
    entity = first.catalog.get("entity:sales.facts")
    assert isinstance(entity, ms.Entity)
    selected = mv.coordinates(by=(mv.identity(entity),), keys=(((1,),),))
    model = tmp_path / "models/semantic/sales/model.py"
    model.write_text(model.read_text().replace("primary_key=['id']", "primary_key=['cost']"))
    second = mv.session.get_or_create("identity-two")
    with pytest.raises(AnalysisError, match="primary-key interpretation"):
        second.frame(selected)
    from marivo.semantic.errors import SemanticError

    with pytest.raises((AnalysisError, SemanticError), match="primary-key interpretation"):
        second.observe({"size": selected.count()})
    metric = second.catalog.get("metric:sales.revenue")
    assert isinstance(metric, ms.Metric)
    with pytest.raises((AnalysisError, SemanticError), match="primary-key interpretation"):
        second.observe(metric, where=mv.in_population(selected))
    with pytest.raises((AnalysisError, SemanticError), match="primary-key interpretation"):
        second.observe({"sampled": metric.per(selected).sum()})


def test_analysis_input_roles_reject_authoring_and_wrong_stage_with_parameter_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.semantic as ms
    from marivo.analysis.errors import AnalysisError

    _write_object_model(tmp_path)
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("role-boundaries")
    metric = session.catalog.get("metric:sales.revenue")
    entity = session.catalog.get("entity:sales.facts")
    assert isinstance(metric, ms.Metric) and isinstance(entity, ms.Entity)
    with pytest.raises(AnalysisError, match="metrics"):
        session.observe(ms.ref.metric("sales.revenue"))
    with pytest.raises(AnalysisError, match=r"by\[0\]"):
        session.observe(metric, by=(metric,))
    with pytest.raises(AnalysisError, match=r"observe\.where"):
        session.observe(metric, where=metric > 0)
    with pytest.raises(AnalysisError, match=r"read\.fields"):
        session.read(session.members(entity), {"wrong": metric})
    frame = session.observe(metric)
    with pytest.raises(AnalysisError, match=r"population_from\.on"):
        session.population_from(frame, on=((None, metric),))
