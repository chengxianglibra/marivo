"""Relationship Populations keep checks at source without downloading ancestors."""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import query_key_fusion
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_population_membership import _session
from tests.datasource.source_cases import source_params
from tests.support.source_trace import capture_source


def _case(
    factory: Callable[..., SemanticProject], root: Path, monkeypatch: pytest.MonkeyPatch
) -> Session:
    session = _session(factory, root, monkeypatch)
    with duckdb.connect(str(root / "population.duckdb")) as db:
        db.execute("DELETE FROM orders")
        db.execute("DELETE FROM links")
        db.execute("INSERT INTO orders SELECT i,i%10,1 FROM range(1000) t(i)")
        db.execute("INSERT INTO links SELECT i,i,i%2+1 FROM range(1000) t(i)")
        db.execute("INSERT INTO links SELECT i+1000,i,i%2+1 FROM range(1000) t(i)")
    return session


def _baseline(query: mv.Query, monkeypatch: pytest.MonkeyPatch) -> pd.DataFrame:
    with monkeypatch.context() as scoped:
        scoped.setattr(
            query_key_fusion, "_fold_population_checks", lambda prepared, lowered: lowered
        )
        return query.execute().to_pandas()


@pytest.mark.parametrize(
    "operation", ("image", "image_count", "literal_preimage", "query_preimage", "empty_query")
)
def test_relationship_population_only_returns_consumed_domain_or_value(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    session = _case(semantic_project_factory, tmp_path, monkeypatch)
    orders = session.members(ms.ref.entity("sales.orders"))
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    image = orders.image(via=path)
    if operation == "image":
        query = session.query(image)
    elif operation == "image_count":
        query = session.observe(
            {
                "v": session.catalog.fields.get("sales.tags.id")
                .count()
                .where(mv.in_population(image))
            }
        )
    else:
        if operation == "literal_preimage":
            targets = mv.identities(session.catalog.entities.get("sales.tags"), keys=((1,),))
        else:
            tags = session.members(ms.ref.entity("sales.tags"))
            fields = session.read(tags, {"label": session.catalog.fields.get("sales.tags.label")})
            targets = fields.where(
                fields["label"] == ("missing" if operation == "empty_query" else "A")
            ).population()
        selected = orders.preimage(targets, via=path)
        query = session.observe(
            {"v": session.catalog.metrics.get("sales.revenue")}, members=selected
        )
    expected = _baseline(query, monkeypatch)
    trace = capture_source(monkeypatch)
    frame = query.execute().to_pandas()
    pd.testing.assert_frame_equal(frame, expected, check_exact=True)
    if operation != "image":
        assert frame[("value", "v")].tolist() == [
            2 if operation == "image_count" else 0 if operation == "empty_query" else 500
        ]
    assert trace.returned_rows == [2 if operation == "image" else 1]
    assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("boundary", ("null_source", "missing_operand", "target_attributes"))
def test_relationship_population_failures_survive_empty_top(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    session = _case(semantic_project_factory, tmp_path, monkeypatch)
    orders = session.members(ms.ref.entity("sales.orders"))
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    if boundary == "null_source":
        with duckdb.connect(str(tmp_path / "population.duckdb")) as db:
            db.execute("INSERT INTO orders VALUES (NULL,0,1)")
        selected = orders.image(via=path)
        metric = (
            session.catalog.fields.get("sales.tags.id").count().where(mv.in_population(selected))
        )
        expected = "non-null complete identity keys"
    elif boundary == "missing_operand":
        selected = mv.identities(
            session.catalog.entities.get("sales.orders"), keys=((9999,),)
        ).image(via=path)
        metric = (
            session.catalog.fields.get("sales.tags.id").count().where(mv.in_population(selected))
        )
        expected = "source relationship row"
    else:
        with duckdb.connect(str(tmp_path / "population.duckdb")) as db:
            db.execute("INSERT INTO tags VALUES (1,'corrupt','other')")
        fields = session.read(
            session.members(ms.ref.entity("sales.tags")),
            {"label": session.catalog.fields.get("sales.tags.label")},
        )
        targets = fields.where(fields["label"] == "missing").population()
        selected = orders.preimage(targets, via=path)
        metric = (
            session.catalog.fields.get("sales.orders.id").count().where(mv.in_population(selected))
        )
        expected = "exactly one attribute row"
    fine = session.observe({"v": metric})
    query = fine.top(0, by=fine["v"])
    with pytest.raises(MaterializationError, match=expected):
        _baseline(query, monkeypatch)
    trace = capture_source(monkeypatch)
    with pytest.raises(MaterializationError, match=expected):
        query.execute()
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []


def test_relationship_member_count_keeps_its_actual_member_input(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _case(semantic_project_factory, tmp_path, monkeypatch)
    orders = session.members(ms.ref.entity("sales.orders"))
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    targets = mv.identities(session.catalog.entities.get("sales.tags"), keys=((1,),))
    selected = orders.preimage(targets, via=path)
    query = session.observe(
        {"n": mv.member_count(), "v": session.catalog.metrics.get("sales.revenue")},
        members=selected,
    )
    expected = _baseline(query, monkeypatch)
    frame = query.execute().to_pandas()
    pd.testing.assert_frame_equal(frame, expected, check_exact=True)
    assert frame[("value", "n")].tolist() == [500]


@pytest.mark.parametrize(
    "backend,profile", source_params([("trino", "iceberg"), ("clickhouse", "mergetree")])
)
def test_remote_relationship_domains_and_query_targets_stay_at_source(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    from contextlib import ExitStack

    from marivo.datasource.ir import TableSourceIR
    from tests.analysis.materialization.test_population_membership import _MODEL
    from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
    from tests.datasource.source_cases import SourceData, source_case

    orders = [{"id": i, "customer": i % 10, "amount": 1} for i in range(1000)]
    links = [{"id": i, "order_id": i % 1000, "tag_id": i % 2 + 1} for i in range(2000)]
    tags = [
        {"id": i, "label": label, "channel": "web"} for i, label in enumerate(("A", "B", "C"), 1)
    ]
    definitions = (
        (
            "orders",
            SourceData(
                "id BIGINT NOT NULL, customer BIGINT, amount BIGINT NOT NULL",
                ",".join(f"({row['id']},{row['customer']},1)" for row in orders),
                "id Int64, customer Int64, amount Int64",
                orders,
            ),
        ),
        (
            "links",
            SourceData(
                "id BIGINT NOT NULL, order_id BIGINT NOT NULL, tag_id BIGINT NOT NULL",
                ",".join(f"({row['id']},{row['order_id']},{row['tag_id']})" for row in links),
                "id Int64, order_id Int64, tag_id Int64",
                links,
            ),
        ),
        (
            "tags",
            SourceData(
                "id BIGINT NOT NULL, label VARCHAR(16), channel VARCHAR(16)",
                "(1,'A','web'),(2,'B','web'),(3,'C','web')",
                "id Int64, label String, channel String",
                tags,
            ),
        ),
    )
    monkeypatch.chdir(tmp_path)
    with ExitStack() as stack:
        model = _MODEL
        first = None
        for name, data in definitions:
            case = stack.enter_context(source_case(backend, profile, tmp_path, monkeypatch, data))
            first = case if first is None else first
            assert isinstance(case.source, TableSourceIR)
            model = model.replace(
                f'md.table("{name}")',
                f"md.table({case.source.table!r},database={case.source.database!r})",
            )
        assert first is not None
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, first, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Data',default=True)\n",
                "sales/objects.py": model,
            }
        )
        session = mv.session.get_or_create("remote-population-fusion", report_timezone="UTC")
        source = session.members(ms.ref.entity("sales.orders"))
        path = (
            session.catalog.relationships.get("sales.order_links"),
            session.catalog.relationships.get("sales.link_tags"),
        )
        image = source.image(via=path)
        trace = capture_source(monkeypatch)
        assert len(session.query(image).execute().to_pandas()) == 2
        count = (
            session.observe(
                {
                    "v": session.catalog.fields.get("sales.tags.id")
                    .count()
                    .where(mv.in_population(image))
                }
            )
            .execute()
            .to_pandas()
        )
        assert count[("value", "v")].tolist() == [2]
        fields = session.read(
            session.members(ms.ref.entity("sales.tags")),
            {"label": session.catalog.fields.get("sales.tags.label")},
        )
        for label, expected in (("A", 500), ("missing", 0)):
            targets = fields.where(fields["label"] == label).population()
            selected = source.preimage(targets, via=path)
            frame = (
                session.observe(
                    {"v": session.catalog.metrics.get("sales.revenue")}, members=selected
                )
                .execute()
                .to_pandas()
            )
            assert frame[("value", "v")].tolist() == [expected]
        assert trace.returned_rows == [2, 1, 1, 1]
        assert trace.uploaded_key_rows == []
