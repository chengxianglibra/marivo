"""Runless source evaluation, exact scope facts and completed-data isolation."""

import gc
import json
import weakref
from collections.abc import Callable
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal

import duckdb
import pandas as pd
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
from marivo._definition.relations import MembershipBinding
from marivo._definition.types import CalendarCoordinate
from marivo._temporal import certify_period_calendar, semantic_grain
from marivo.analysis.core.artifact_data import DataColumn, NamedCalendarData
from marivo.analysis.core.data_types import ScalarDataType, TimestampDataType
from marivo.analysis.core.frame_calendar import freeze_calendar
from marivo.analysis.core.frame_data import MemoryFrameBacking, StoredFrameBacking
from marivo.analysis.core.frame_source_facts import source_scope
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.core.output_records import OutputEvidence
from marivo.analysis.dsl import frame_sources
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.population import _calendar_population
from marivo.analysis.dsl.temporal import exact_scope
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.refs import ref
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_population_membership import _session as membership_session
from tests.shared_fixtures import DslCaseFactory
from tests.support.json import arr, checked


@pytest.fixture
def session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "frame-source.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE facts(tenant VARCHAR, id BIGINT, channel VARCHAR, numerator BIGINT, denominator BIGINT, item BIGINT)"
        )
        connection.execute(
            "INSERT INTO facts VALUES ('t1',1,'Web',450,45,1),('t1',2,'Web',450,45,2),"
            "('t2',1,'Store',50,1,2),('t2',2,'Store',50,0,3)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table('facts'), primary_key=['tenant','id'])
channel = ms.dimension_column(name='channel', entity=facts, column='channel')
item = ms.dimension_column(name='item', entity=facts, column='item')
numerator = ms.measure_column(name='numerator', entity=facts, column='numerator', additivity=ms.additive_all(), unit='USD')
denominator = ms.measure_column(name='denominator', entity=facts, column='denominator', additivity=ms.additive_all(), unit='1')
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.create("frame-evaluation", timezone="UTC")


def _fact_scopes(facts: tuple[OutputEvidence, ...]) -> list[object]:
    scopes: list[object] = []
    for fact in facts:
        if fact.kind != "read":
            continue
        encoded = dict(fact.values)["source_scopes"]
        assert isinstance(encoded, str)
        for item in arr(checked(json.loads(encoded))):
            scope = arr(item)[1]
            assert isinstance(scope, str)
            scopes.append(json.loads(scope))
    return scopes


def _memory(frame: AnalysisFrame) -> MemoryFrameBacking:
    assert isinstance(frame._backing, MemoryFrameBacking)
    return frame._backing


def test_literal_joint_identity_coordinates_preserve_complete_tuples(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    entity = session.catalog.entities.get("sales.facts")
    channel = session.catalog.fields.get("sales.facts.channel")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("literal coordinate construction must not read a source")

    with monkeypatch.context() as guard:
        guard.setattr(SourceSession, "__enter__", forbidden)
        population = mv.coordinates(
            by=(mv.identity(entity), channel),
            keys=((("t1", 1), "Web"), (("missing", 999), None), (("t1", 1), "Web")),
        )
    frame = session.frame(population).compute().to_pandas()
    assert [
        (key, None if pd.isna(value) else value)
        for key, value in frame.itertuples(index=False, name=None)
    ] == [
        (("missing", 999), None),
        (("t1", 1), "Web"),
    ]
    counts = session.observe({"n": population.count()}, by=(channel,)).compute().to_pandas()
    assert counts[("value", "n")].tolist() == [1, 1]
    assert session.observe({"n": population.count()}).compute().to_pandas()[
        ("value", "n")
    ].tolist() == [2]


def _observe(
    session: Session, *, grouped: bool = False, where: Deferred | None = None
) -> AnalysisFrame:
    numerator = session.catalog.fields.get("sales.facts.numerator")
    denominator = session.catalog.fields.get("sales.facts.denominator")
    channel = session.catalog.fields.get("sales.facts.channel")
    return frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {"ratio": numerator.sum() / denominator.sum()},
        by=(channel,) if grouped else (),
        where=where,
    )


def _inventory(session: Session) -> tuple[tuple[str, str], ...]:
    root = session.project_root / ".marivo"
    return tuple(
        sorted(
            (str(path.relative_to(root)), sha256(path.read_bytes()).hexdigest())
            for path in root.rglob("*")
            if path.is_file()
            and not (path.parent.name == "logs" and path.name.startswith("execution-"))
        )
    )


def _contains_scope(value: object, target: object) -> bool:
    if value == target:
        return True
    if isinstance(value, dict):
        return any(_contains_scope(item, target) for item in value.values())
    if isinstance(value, list):
        return any(_contains_scope(item, target) for item in value)
    return False


def test_source_recompute_sees_mutation_and_fixed_data_does_not(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _observe(session, grouped=True)
    before = _inventory(session)
    fixed = source.compute()
    assert _inventory(session) == before
    assert fixed.to_pandas()[("value", "ratio")].tolist() == [100.0, 10.0]
    with duckdb.connect(str(session.project_root / "frame-source.duckdb")) as connection:
        connection.execute("UPDATE facts SET numerator=numerator+denominator")
    fresh = source.compute()
    assert fresh.to_pandas()[("value", "ratio")].tolist() == [101.0, 11.0]
    assert _memory(fixed).metadata.result_id != _memory(fresh).metadata.result_id

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("fixed data must not prepare sources")

    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.open_query_sources", forbidden
    )
    reduced = fixed.reduce({"mean": fixed["ratio"].mean()}).compute()
    assert reduced.to_pandas()[("value", "mean")].tolist() == [55.0]
    assert (
        _memory(reduced).metadata.audit.source_facts == _memory(fixed).metadata.audit.source_facts
    )
    assert _memory(reduced).metadata.audit.evaluation != _memory(fixed).metadata.audit.evaluation
    assert len(session.runs().items) == 0


def test_current_row_mean_does_not_recover_original_ratio(session: Session) -> None:
    grouped = _observe(session, grouped=True).compute()
    row_mean = grouped.reduce({"mean": grouped["ratio"].mean()}).compute()
    original = _observe(session).compute()
    assert row_mean.to_pandas()[("value", "mean")].tolist() == [55.0]
    assert original.to_pandas()[("value", "ratio")].iloc[0] == pytest.approx(1000 / 91)


def test_current_distinct_counts_cannot_recover_original_distinct(session: Session) -> None:
    item = session.catalog.fields.get("sales.facts.item")
    channel = session.catalog.fields.get("sales.facts.channel")

    def source(*, grouped: bool) -> AnalysisFrame:
        return frame_sources.observe(
            session._runtime.local,
            session.catalog._state,
            session._runtime.connection_config,
            {"items": item.count_distinct()},
            by=(channel,) if grouped else (),
        )

    groups = ({1, 2}, {2, 3})
    grouped = source(grouped=True).compute()
    current = grouped.reduce({"items": grouped["items"].sum()}).compute()
    original = source(grouped=False).compute()
    assert grouped.to_pandas()[("value", "items")].tolist() == [2, 2]
    assert current.to_pandas()[("value", "items")].tolist() == [sum(map(len, groups))] == [4]
    assert original.to_pandas()[("value", "items")].tolist() == [len(set.union(*groups))] == [3]


def test_source_close_failure_and_numeric_failure_publish_nothing(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _observe(session)
    before = _inventory(session)
    invalid = source.select({"invalid": source["ratio"].divide(0, zero_denominator="error")})
    with pytest.raises(MaterializationError, match="nonzero denominator"):
        invalid.compute()
    assert _inventory(session) == before and session.runs().items == ()
    close = SourceSession.close

    def fail_close(self: SourceSession) -> None:
        close(self)
        raise RuntimeError("injected close failure")

    monkeypatch.setattr(SourceSession, "close", fail_close)
    with pytest.raises(MaterializationError, match="source cleanup"):
        source.compute()
    assert _inventory(session) == before and session.runs().items == ()
    with pytest.raises(MaterializationError, match="source cleanup") as failure:
        invalid.compute()
    assert failure.value.stage == "frame_compute"
    cleanup = failure.value.__cause__
    assert isinstance(cleanup, RuntimeError) and str(cleanup) == "injected close failure"
    numeric = cleanup.__context__
    assert isinstance(numeric, MaterializationError)
    assert isinstance(numeric.__cause__, SemanticExpressionError)
    assert _inventory(session) == before and session.runs().items == ()


def test_source_numeric_failure_has_analysis_owner_and_closes_without_run(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    numerator = session.catalog.fields.get("sales.facts.numerator")
    denominator = session.catalog.fields.get("sales.facts.denominator")
    source = frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {
            "invalid": numerator.sum().divide(
                denominator.sum() - denominator.sum(), zero_denominator="error"
            )
        },
    )
    before = _inventory(session)
    closed: list[SourceSession] = []
    close = SourceSession.close

    def record_close(self: SourceSession) -> None:
        close(self)
        closed.append(self)

    monkeypatch.setattr(SourceSession, "close", record_close)
    with pytest.raises(MaterializationError, match="nonzero denominator") as failure:
        source.compute()
    error = failure.value
    cause = error.__cause__
    assert isinstance(cause, SemanticExpressionError)
    assert error.stage == "numeric_value" and error.run_ref is None
    assert error.expected == cause.expected and error.received == cause.received
    assert error.repair is not None and cause.repair is not None
    assert error.repair.action == cause.repair.action
    assert error.repair.help_target.canonical_id == "analysis_frame.compute"
    assert error.received is not None
    assert "source=sales.facts" in error.received
    assert closed
    assert _inventory(session) == before and session.runs().items == ()


def test_pending_source_show_discloses_captured_scope_without_execution(
    session: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    channel = session.catalog.fields.get("sales.facts.channel")
    web = _observe(session, where=channel == "Web")
    store = _observe(session, where=channel == "Store")
    before = _inventory(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("metadata display must not prepare, evaluate or read")

    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.evaluate_frame", forbidden
    )
    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.open_query_sources", forbidden
    )
    monkeypatch.setattr("marivo.datasource.runtime.DatasourceConnectionConfig.operation", forbidden)
    web.show(n=0)
    web_text = capsys.readouterr().out
    store.show(n=20)
    store_text = capsys.readouterr().out
    assert "scope:" in web_text and "Web" in web_text and "Store" not in web_text
    assert "scope:" in store_text and "Store" in store_text and "Web" not in store_text
    assert "source" in web_text and "operation: observe" in web_text
    assert _inventory(session) == before and session.runs().items == ()


def test_pending_scope_summary_keeps_interval_count_domains_and_joint_keys_without_reads(
    session: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    entity = session.catalog.entities.get("sales.facts")
    first = mv.coordinates(by=(mv.identity(entity),), keys=((("t1", 1),), (("missing", 999),)))
    second = mv.coordinates(by=(mv.identity(entity),), keys=((("t2", 2),),))
    scope = session.time_scope(start="2026-08-01", end="2026-09-01", timezone="Asia/Shanghai")
    domain = mv.product(scope, first)
    counted = session.observe({"first": first.count(), "second": second.count()}, by=domain)
    keys = session.frame(domain)
    before = _inventory(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("captured scope display must not prepare, evaluate or read")

    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.evaluate_frame", forbidden
    )
    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.open_query_sources", forbidden
    )
    monkeypatch.setattr("marivo.datasource.runtime.DatasourceConnectionConfig.operation", forbidden)
    for value in (counted, keys):
        value.show(n=0)
        text = capsys.readouterr().out
        assert "PopulationProduct factors=2; joint tuples preserved" in text
        assert "('missing', 999)" in text and "('t1', 1)" in text
        assert "2026-07-31T16:00:00+00:00" in text and "2026-08-31T16:00:00+00:00" in text
        assert "Asia/Shanghai" in text and "interval" in text and "identity" in text
        assert "owner_id" not in text and "sha256:" not in text
        if value is counted:
            assert "count[0].domain" in text and "count[1].domain" in text
            assert "('t2', 2)" in text
    assert _inventory(session) == before and session.runs().items == ()


def test_pending_count_scope_distinguishes_literal_entities_with_the_same_keys(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    customers = mv.coordinates(
        by=(mv.identity(case.catalog.entities.get("sales.customer")),), keys=((("same",),),)
    )
    orders = mv.coordinates(
        by=(mv.identity(case.catalog.entities.get("sales.order")),), keys=((("same",),),)
    )
    pending = case.session.observe({"customers": customers.count(), "orders": orders.count()})

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("count scope display consulted a source")

    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.evaluate_frame", forbidden
    )
    monkeypatch.setattr("marivo.datasource.runtime.DatasourceConnectionConfig.operation", forbidden)
    pending.show(n=0)
    text = capsys.readouterr().out
    assert "count[0].domain" in text and "count[1].domain" in text
    assert "identity:sales.customer" in text and "identity:sales.order" in text
    assert "('same',)" in text
    assert case.session.runs().items == ()


def test_pending_count_scope_distinguishes_fixed_frame_inputs_without_reading_rows(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    entity = case.catalog.entities.get("sales.customer")
    first = case.session.frame(
        mv.coordinates(by=(mv.identity(entity),), keys=((("first",),),))
    ).compute()
    second = case.session.frame(
        mv.coordinates(by=(mv.identity(entity),), keys=((("second",),),))
    ).compute()
    domains = tuple(
        case.session.population_from(frame, on=((frame.axes[0], entity),))
        for frame in (first, second)
    )
    pending = case.session.observe({"first": domains[0].count(), "second": domains[1].count()})

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("count scope display read captured fixed rows")

    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.evaluate_frame", forbidden
    )
    monkeypatch.setattr("marivo.analysis.core.frame_data.CompletedFrameData.copy_rows", forbidden)
    pending.show(n=0)
    text = capsys.readouterr().out
    assert "count[0].domain" in text and "count[1].domain" in text
    assert first._input_id in text and second._input_id in text
    assert "FrameKeys input=" in text and "identity:sales.customer" in text
    assert "axis.0" in text and "customer_id" in text
    assert case.session.runs().items == ()


def test_source_fact_filters_and_special_complete_keys_are_lossless(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    channel = session.catalog.fields.get("sales.facts.channel")
    tail = "long-value-end"
    long_value = "x" * 6000 + tail
    candidates = (*(f"choice_{index}_abcdefgh" for index in range(400)), "Web", long_value)
    source = _observe(session, where=channel.isin(candidates))
    source.show(n=0)
    card = capsys.readouterr().out
    assert "omitted" in card and len(card.encode("utf-8")) <= 8192
    fixed = source.compute()
    facts = _memory(fixed).metadata.audit.source_facts
    serialized = str(tuple(fact.values for fact in facts))
    assert long_value in serialized and candidates[-3] in serialized
    assert "... omitted" not in serialized
    entity = session.catalog.entities.get("sales.facts")
    selected = mv.coordinates(by=(mv.identity(entity),), keys=((("t1", 1),), (("t2", 1),)))
    key_fact = source_scope(selected._definition)
    assert '"t1"' in key_fact and '"t2"' in key_fact and '"0x1"' in key_fact
    restricted = _observe(session, where=mv.in_population(selected)).compute()
    key_scope = json.loads(key_fact)
    actual_scopes = _fact_scopes(_memory(restricted).metadata.audit.source_facts)

    assert any(_contains_scope(scope, key_scope) for scope in actual_scopes)
    other = _observe(session, where=channel == "Store").compute()
    assert _memory(fixed).metadata.audit.source_facts != _memory(other).metadata.audit.source_facts


def test_general_population_composite_identity_frame(session: Session) -> None:
    entity = session.catalog.entities.get("sales.facts")
    population = mv.coordinates(by=(mv.identity(entity),), keys=((("t2", 1),), (("t1", 1),)))
    result = frame_sources.frame(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        population,
    ).compute()
    assert result.output_names == () and _memory(result).data.row_count == 2
    assert result.axes[0].role == "identity" and len(result.axes[0].key_names) == 2


def test_read_retains_complete_population_scope_without_producer_formula(session: Session) -> None:
    entity = session.catalog.entities.get("sales.facts")
    population = mv.coordinates(by=(mv.identity(entity),), keys=((("t2", 1),), (("t1", 2),)))
    result = frame_sources.read(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        population,
        {"channel": session.catalog.fields.get("sales.facts.channel")},
    ).compute()
    assert result.to_pandas()[("value", "channel")].tolist() == ["Web", "Store"]
    facts = _memory(result).metadata.audit.source_facts
    assert any(
        _contains_scope(scope, json.loads(source_scope(population._definition)))
        for scope in _fact_scopes(facts)
    )


@pytest.mark.parametrize("empty", [False, True])
def test_general_population_zero_axis_preserves_empty_or_singleton(
    session: Session, empty: bool
) -> None:

    if empty:
        with duckdb.connect(str(session.project_root / "frame-source.duckdb")) as connection:
            connection.execute("DELETE FROM facts")
    population = session.members(session.catalog.entities.get("sales.facts")).project(by=())
    result = frame_sources.frame(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        population,
    ).compute()
    assert result.axes == () and result.output_names == ()
    assert result.to_pandas().shape == (0 if empty else 1, 0)


def test_complete_memory_releases_source_scope_and_runtime(session: Session) -> None:
    from marivo.analysis.materialization.admission import DatasetRuntime

    runtime = DatasetRuntime.create(session.project_root, "independent-runtime", timezone="UTC")
    reference = weakref.ref(runtime)
    numerator = session.catalog.fields.get("sales.facts.numerator")
    source = frame_sources.observe(
        runtime.local, session.catalog._state, runtime.connection_config, {"value": numerator.sum()}
    )
    fixed = source.compute()
    del source, runtime
    gc.collect()
    assert isinstance(fixed._backing, MemoryFrameBacking)
    assert not hasattr(fixed._backing, "root")
    assert reference() is None
    assert _memory(fixed.select({"twice": fixed["value"] * 2}).compute()).data.row_count == 1


def test_source_identity_rows_cannot_hide_null_complete_key(session: Session) -> None:
    from marivo.analysis.errors import AnalysisError

    population = session.members(session.catalog.entities.get("sales.facts"))
    source = frame_sources.frame(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        population,
    )
    with duckdb.connect(str(session.project_root / "frame-source.duckdb")) as connection:
        connection.execute("UPDATE facts SET tenant=NULL WHERE tenant='t1'")
    with pytest.raises((AnalysisError, SemanticExpressionError), match=r"key|identity"):
        source.compute()
    assert session.runs().items == ()


def test_calendar_freeze_preserves_dst_fold_and_partial_nominal_bounds() -> None:
    fold = exact_scope("2024-11-03", "2024-11-04", "America/New_York").by(mv.grain("hour"))
    coordinate = _calendar_population(fold)._definition.axes[0]
    assert isinstance(coordinate, CalendarCoordinate)
    assert coordinate.grain_spec is fold.grid.grain_spec
    assert coordinate.grain == fold.grid.grain_token == "hour"
    key = DataColumn("key.0", "bucket", ScalarDataType("int64"), False)
    frozen = freeze_calendar(coordinate, token="axis.0", name="hour", key=key)
    rows = frozen.directory.rows
    labels = rows["nominal_label"].tolist()
    assert len(labels) == len(set(labels)) == 25
    assert "2024-11-03T01:00:00.000000-04:00" in labels
    assert "2024-11-03T01:00:00.000000-05:00" in labels
    assert rows["ordinal"].tolist() == list(range(25))
    assert rows["key.0"].tolist() == list(range(25))
    assert all(not column.nullable for column in frozen.directory.schema.columns)

    partial = exact_scope("2026-08-15", "2026-09-01", "UTC").by(mv.grain("month"))
    coordinate = _calendar_population(partial)._definition.axes[0]
    assert isinstance(coordinate, CalendarCoordinate)
    rows = freeze_calendar(coordinate, token="axis.0", name="month", key=key).directory.rows
    assert rows["nominal_label"].tolist() == ["2026-08-01"]
    assert rows["nominal_start"].iloc[0].isoformat() == "2026-08-01T00:00:00+00:00"
    assert rows["actual_start"].iloc[0].isoformat() == "2026-08-15T00:00:00+00:00"


def test_named_calendar_freeze_captures_exact_level_and_canonical_labels() -> None:
    start, end = date(2026, 1, 1), date(2026, 1, 9)
    calendar = ref.period_calendar("sales.fiscal")
    snapshot = certify_period_calendar(
        calendar_ref=calendar,
        boundary_timezone="UTC",
        coverage=(start, end),
        rows=(
            {"date": start + timedelta(days=i), "short": i // 2, "long": f"P{i // 4}"}
            for i in range(8)
        ),
        levels={"short": "short", "long": "long"},
    )
    key = DataColumn("key.0", "bucket", ScalarDataType("int64"), False)
    for level, labels in (("short", ["0", "1", "2", "3"]), ("long", ['"P0"', '"P1"'])):
        grain = semantic_grain(calendar=calendar, level=level, snapshot=snapshot)
        axis = exact_scope(start, end, "UTC").by(grain)
        coordinate = _calendar_population(axis)._definition.axes[0]
        assert isinstance(coordinate, CalendarCoordinate)
        frozen = freeze_calendar(coordinate, token="axis.0", name=level, key=key)
        assert frozen.axis.frozen is not None
        assert frozen.axis.frozen.definition == NamedCalendarData(
            "sales.fiscal", snapshot.snapshot_digest, level
        )
        assert frozen.directory.rows["nominal_label"].tolist() == labels
        assert frozen.directory.rows["ordinal"].tolist() == list(range(len(labels)))
    day = exact_scope(start, start + timedelta(days=1), "UTC").by(
        semantic_grain(calendar=calendar, level="day", snapshot=snapshot)
    )
    coordinate = _calendar_population(day)._definition.axes[0]
    assert isinstance(coordinate, CalendarCoordinate)
    frozen = freeze_calendar(coordinate, token="axis.0", name="day", key=key)
    assert frozen.directory.rows["nominal_label"].tolist() == ['"2026-01-01"']


def test_source_top_keeps_entire_frozen_calendar_directory(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    calendar = session.time_scope(start="2026-08-01", end="2026-09-01").by(mv.grain("day"))
    source = frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {"sales": session.catalog.metrics.get("sales.revenue")},
        by=(calendar,),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("calendar facts must already be frozen")

    monkeypatch.setattr(frame_sources, "freeze_calendar", forbidden)
    source.show(n=20)
    result = source.top(1, by=source["sales"]).compute()
    assert _memory(result).data.row_count == 1
    (directory,) = _memory(result).data.copy_attachments()
    assert directory.rows["ordinal"].tolist() == list(range(31))
    assert directory.rows["nominal_label"].tolist() == [f"2026-08-{day:02}" for day in range(1, 32)]
    assert directory.schema.axes[0].columns == result._schema.axes[0].columns
    boundary_names = ("nominal_start", "nominal_end", "actual_start", "actual_end")
    assert {
        column.name: column.dtype
        for column in directory.schema.values
        if column.name in boundary_names
    } == {name: TimestampDataType("us", "UTC") for name in boundary_names}


@pytest.mark.parametrize("unmatched", ("exclude", "null"))
def test_membership_scope_encodes_only_the_complete_closed_binding(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unmatched: Literal["exclude", "null"],
) -> None:
    session = membership_session(semantic_project_factory, tmp_path, monkeypatch)
    label = session.catalog.fields.get("sales.tags.label")
    channel = session.catalog.fields.get("sales.tags.channel")
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    public = mv.membership((label, channel), via=(path,), unmatched=unmatched)
    base = public._binding
    assert isinstance(base, MembershipBinding)
    encoded = source_scope(public)
    assert encoded == source_scope(base)
    assert json.loads(encoded) == {
        "scope": "MembershipBinding",
        "fields": json.loads(source_scope(base.fields)),
        "paths": json.loads(source_scope(base.paths)),
        "unmatched": json.loads(source_scope(unmatched)),
    }
    assert len(base.fields) == 2 and tuple(map(len, base.paths)) == (2,)
    for key in ("sales.tags.label", "sales.tags.channel", "sales.order_links", "sales.link_tags"):
        assert key in encoded
    ordinary = base.fields[0]
    assert json.loads(source_scope(ordinary)) == {
        "scope": "FieldRead",
        "field": ["reference", ordinary.field.ref.key, ordinary.field.version],
        "entity": ["reference", ordinary.entity.ref.key, ordinary.entity.version],
        "path": [],
        "at": ["none", ""],
    }
    for unsupported in (object(), NamedCalendarData("sales.fiscal", "digest", "month")):
        with pytest.raises(CoreRuleError, match="supported inert source constraint"):
            source_scope(unsupported)


def test_membership_source_audit_survives_fixed_and_persisted_results_without_replay(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = membership_session(semantic_project_factory, tmp_path, monkeypatch)
    with duckdb.connect(str(tmp_path / "population.duckdb")) as connection:
        connection.execute("UPDATE orders SET amount=10 WHERE id=2")
    revenue = session.catalog.metrics.get("sales.revenue")
    label = session.catalog.fields.get("sales.tags.label")
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    buckets = mv.membership(label, via=(path,), unmatched="exclude")
    domain = mv.coordinates(by=(label,), keys=(("A",), ("B",), ("C",)))
    pending = session.observe({"revenue": revenue}, by=domain, memberships=(buckets,))
    before = _inventory(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("captured metadata and fixed results must not replay a source")

    with monkeypatch.context() as guard:
        guard.setattr("marivo.analysis.materialization.frame_evaluation.evaluate_frame", forbidden)
        guard.setattr(
            "marivo.analysis.materialization.frame_evaluation.open_query_sources", forbidden
        )
        guard.setattr("marivo.datasource.runtime.DatasourceConnectionConfig.operation", forbidden)
        pending.show(n=0)
    card = capsys.readouterr().out
    assert "MembershipBinding" in card and "sales.tags.label" in card
    assert "sales.order_links" in card and "sales.link_tags" in card
    assert _inventory(session) == before and session.runs().items == ()
    fixed = pending.compute()
    assert fixed.to_pandas()[("value", "revenue")].tolist() == [20, 10, 0]
    assert _inventory(session) == before and session.runs().items == ()
    metadata = _memory(fixed).metadata
    facts = metadata.audit.source_facts
    scope = json.loads(source_scope(buckets))
    assert any(_contains_scope(value, scope) for value in _fact_scopes(facts))
    assert len(facts) == len(set(facts))
    assert not hasattr(_memory(fixed), "root") and not hasattr(_memory(fixed), "graph")
    assert all(not callable(value) for fact in facts for _, value in fact.values)

    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.open_query_sources", forbidden
    )
    monkeypatch.setattr("marivo.datasource.runtime.DatasourceConnectionConfig.operation", forbidden)
    aligned = fixed.align(fixed, on=((fixed.axes[0], fixed.axes[0]),)).compute()
    assert _memory(aligned).metadata.audit.source_facts == facts
    assert len(_memory(aligned).metadata.audit.source_facts) == len(set(facts))
    assert session.runs().items == ()
    saved = fixed.persist()
    assert isinstance(saved._backing, StoredFrameBacking)
    assert saved._backing.metadata == metadata
    assert len(session.runs().items) == 1
    reopened = session.persisted_frame(saved._backing.artifact_id)
    assert isinstance(reopened._backing, StoredFrameBacking)
    assert reopened._backing.metadata == metadata
    loaded = reopened.compute()
    assert _memory(loaded).metadata == metadata
    assert loaded.to_pandas()[("value", "revenue")].tolist() == [20, 10, 0]
    assert len(session.runs().items) == 1
