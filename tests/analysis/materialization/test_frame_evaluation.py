"""Runless source evaluation, exact scope facts and completed-data isolation."""

import gc
import json
import weakref
from collections.abc import Callable
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
from marivo._definition.types import CalendarCoordinate
from marivo._temporal import certify_period_calendar, semantic_grain
from marivo.analysis.core.artifact_data import DataColumn, NamedCalendarData
from marivo.analysis.core.data_types import ScalarDataType, TimestampDataType
from marivo.analysis.core.frame_calendar import freeze_calendar
from marivo.analysis.core.frame_data import MemoryFrameBacking
from marivo.analysis.core.frame_source_facts import source_scope
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
from tests.shared_fixtures import DslCaseFactory


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
    return mv.session.get_or_create("frame-evaluation", report_timezone="UTC")


def _observe(session: Session, *, grouped: bool = False, where: object = None) -> AnalysisFrame:
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
    assert fixed._backing.metadata.result_id != fresh._backing.metadata.result_id

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("fixed data must not prepare sources")

    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.open_query_sources", forbidden
    )
    reduced = fixed.reduce({"mean": fixed["ratio"].mean()}).compute()
    assert reduced.to_pandas()[("value", "mean")].tolist() == [55.0]
    assert (
        reduced._backing.metadata.audit.source_facts == fixed._backing.metadata.audit.source_facts
    )
    assert reduced._backing.metadata.audit.evaluation != fixed._backing.metadata.audit.evaluation
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
    with pytest.raises(SemanticExpressionError):
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
    assert isinstance(cleanup.__context__, SemanticExpressionError)
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
    assert "omitted (display budget" in card and len(card.encode("utf-8")) <= 8192
    fixed = source.compute()
    facts = fixed._backing.metadata.audit.source_facts
    serialized = str(tuple(fact.values for fact in facts))
    assert long_value in serialized and candidates[-3] in serialized
    assert "... omitted" not in serialized
    entity = session.catalog.entities.get("sales.facts")
    selected = mv.identities(entity, keys=(("t1", 1), ("t2", 1)))
    key_fact = source_scope(selected.definition)
    assert '"t1"' in key_fact and '"t2"' in key_fact and '"0x1"' in key_fact
    restricted = _observe(session, where=mv.in_population(selected)).compute()
    key_scope = json.loads(key_fact)
    actual_scopes = [
        json.loads(token[1])
        for fact in restricted._backing.metadata.audit.source_facts
        if fact.kind == "read"
        for token in json.loads(dict(fact.values)["source_scopes"])
    ]

    assert any(_contains_scope(scope, key_scope) for scope in actual_scopes)
    other = _observe(session, where=channel == "Store").compute()
    assert fixed._backing.metadata.audit.source_facts != other._backing.metadata.audit.source_facts


def test_general_population_composite_identity_frame(session: Session) -> None:
    entity = session.catalog.entities.get("sales.facts")
    population = mv.identities(entity, keys=(("t2", 1), ("t1", 1)))
    result = frame_sources.frame(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        population,
    ).compute()
    assert result.output_names == () and result._backing.data.row_count == 2
    assert result.axes[0].role == "identity" and len(result.axes[0].key_names) == 2


def test_read_retains_complete_population_scope_without_producer_formula(session: Session) -> None:
    entity = session.catalog.entities.get("sales.facts")
    population = mv.identities(entity, keys=(("t2", 1), ("t1", 2)))
    result = frame_sources.read(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        population,
        {"channel": session.catalog.fields.get("sales.facts.channel")},
    ).compute()
    assert result.to_pandas()[("value", "channel")].tolist() == ["Web", "Store"]
    facts = result._backing.metadata.audit.source_facts
    assert any(
        _contains_scope(json.loads(token[1]), json.loads(source_scope(population.definition)))
        for fact in facts
        if fact.kind == "read"
        for token in json.loads(dict(fact.values)["source_scopes"])
    )


@pytest.mark.parametrize("empty", [False, True])
def test_general_population_zero_axis_preserves_empty_or_singleton(
    session: Session, empty: bool
) -> None:
    import marivo.semantic as ms

    if empty:
        with duckdb.connect(str(session.project_root / "frame-source.duckdb")) as connection:
            connection.execute("DELETE FROM facts")
    population = session.members(ms.ref.entity("sales.facts")).project(by=())
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

    runtime = DatasetRuntime.create(
        session.project_root, "independent-runtime", report_timezone="UTC"
    )
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
    assert fixed.select({"twice": fixed["value"] * 2}).compute()._backing.data.row_count == 1


def test_source_identity_rows_cannot_hide_null_complete_key(session: Session) -> None:
    import marivo.semantic as ms
    from marivo.analysis.errors import AnalysisError

    population = session.members(ms.ref.entity("sales.facts"))
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
    coordinate = _calendar_population(fold).definition.axes[0]
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
    coordinate = _calendar_population(partial).definition.axes[0]
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
        coordinate = _calendar_population(axis).definition.axes[0]
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
    coordinate = _calendar_population(day).definition.axes[0]
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
        during=calendar,
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("calendar facts must already be frozen")

    monkeypatch.setattr(frame_sources, "freeze_calendar", forbidden)
    source.show(n=20)
    result = source.top(1, by=source["sales"]).compute()
    assert result._backing.data.row_count == 1
    (directory,) = result._backing.data.copy_attachments()
    assert directory.rows["ordinal"].tolist() == list(range(31))
    assert directory.rows["nominal_label"].tolist() == [f"2026-08-{day:02}" for day in range(1, 32)]
    assert directory.schema.axes[0].columns == result._schema.axes[0].columns
    assert all(
        column.dtype == TimestampDataType("us", "UTC") for column in directory.schema.values[2:]
    )
