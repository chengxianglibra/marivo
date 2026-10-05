"""Governed Lifecycle projects for public and independent-process acceptance."""

from datetime import UTC, datetime, timedelta
from typing import Literal

import ibis
import pyarrow as pa
import pyarrow.parquet as pq

START = datetime(2026, 2, 1, tzinfo=UTC)
END = START + timedelta(seconds=100)
TRIGGERS = ("started", "paid", "pulse", "finished")


def keys(profile, prefix, count):
    if profile == "s":
        return {prefix: [f"{prefix}{i}" for i in range(count)]}
    if profile == "i":
        return {prefix: [9007199254740993 + i for i in range(count)]}
    return {
        prefix: [f"{prefix}{i}" for i in range(count)],
        prefix + "2": [9007199254740993 + i for i in range(count)],
    }


def build_lifecycle_public(
    root,
    *,
    subject="i",
    occurrence="i",
    form="table",
    unit="us",
    zone="UTC",
    ordered=True,
    rows=None,
    empty=False,
    cycle=False,
    observations=False,
    backend_name: Literal["duckdb", "sqlite"] = "duckdb",
):
    import marivo.analysis as mv
    import marivo.semantic as ms
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.store import SessionStore
    from marivo.analysis.session.core import Session

    root.mkdir(parents=True, exist_ok=True)
    members = keys(subject, "sid", 3)
    # subject index, event kind, relative seconds, business sequence
    values = (
        rows
        if rows is not None
        else [
            (0, "paid", -20, 1),
            (0, "started", -10, 2),
            (0, "pulse", 5, 3),
            (0, "started", 6, 4),
            (0, "paid", 10, 5),
            (0, "finished", 10, 6),
            (0, "paid", 15, 7),
            (0, "started", 15, 8),
            (1, "started", 0, 1),
            (1, "finished", 100, 2),
        ]
    )
    identities = keys(occurrence, "oid", len(values))
    facts = dict(identities)
    facts.update({name: [column[value[0]] for value in values] for name, column in members.items()})
    factor = {"s": 1, "ms": 1000, "us": 1000000, "ns": 1000000000}[unit]
    epoch = int(START.timestamp()) * factor
    facts.update(kind=[value[1] for value in values], seq=[value[3] for value in values])
    facts["instant"] = pa.array(
        [epoch + int(value[2] * factor) + (123 if unit == "ns" else 0) for value in values],
        type=pa.timestamp(unit, tz="UTC"),
    )
    if observations:
        facts["amount"] = pa.array([float(value[3]) for value in values], type=pa.float64())
    tables = {"subjects": pa.table(members), "facts": pa.table(facts)}
    if empty:
        tables["subjects"] = tables["subjects"].slice(0, 0)
        tables["facts"] = tables["facts"].slice(0, 0)
    database = root / ("source." + backend_name)
    if backend_name == "sqlite":
        if form != "table" or unit != "us" or zone != "UTC":
            raise ValueError("SQLite Lifecycle fixtures require native UTC-us tables")
        tables = {
            name: table.cast(
                pa.schema(
                    [
                        pa.field(
                            field.name,
                            pa.timestamp("us") if pa.types.is_timestamp(field.type) else field.type,
                        )
                        for field in table.schema
                    ]
                )
            )
            for name, table in tables.items()
        }
    backend = (
        ibis.duckdb.connect(database) if backend_name == "duckdb" else ibis.sqlite.connect(database)
    )
    try:
        for name, table in tables.items():
            backend.create_table(name, table)
            if form == "parquet":
                pq.write_table(table, root / (name + ".parquet"))
        if backend_name == "sqlite":
            backend.con.commit()
    finally:
        backend.disconnect()
    models = root / "models"
    (models / "semantic" / "commerce").mkdir(parents=True)
    (models / "datasources").mkdir()
    (root / "marivo.toml").write_text('[project]\nname="r75"\n')
    (models / "datasources" / "warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.{backend_name}(name='warehouse', path={str(database)!r})\n"
    )
    (models / "semantic" / "commerce" / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='commerce', owner='Analytics', default=True)\n"
    )

    def source(name):
        return (
            f"md.table({name!r})"
            if form == "table"
            else f"md.parquet({str(root / (name + '.parquet'))!r})"
        )

    code = f"""import marivo.datasource as md
import marivo.semantic as ms
subjects = ms.entity(name='subjects', datasource=ms.ref.datasource('warehouse'), source={source("subjects")}, primary_key={list(members)!r})
facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source={source("facts")}, primary_key={list(identities)!r})
"""
    for name in members:
        code += f"subject_{name} = ms.dimension_column(name={name!r}, entity=subjects, column={name!r})\nfact_{name} = ms.dimension_column(name={name!r}, entity=facts, column={name!r})\n"
    for name in identities:
        code += f"{name} = ms.dimension_column(name={name!r}, entity=facts, column={name!r})\n"
    code += f"""kind = ms.dimension_column(name='kind', entity=facts, column='kind')
seq = ms.dimension_column(name='seq', entity=facts, column='seq')
instant = ms.time_dimension_column(name='instant', entity=facts, column='instant', granularity='second', parse=ms.timestamp(timezone='UTC'))
participant = ms.relationship(name='participant', from_entity=facts, to_entity=subjects, keys=[{", ".join(f"ms.join_on(fact_{name}, subject_{name})" for name in members)}])
"""
    for event in TRIGGERS:
        code += f"""@ms.event(name={event!r}, identity=({", ".join(identities)},), occurred_at=instant,
    participants=(ms.participant(name='subject', path=(participant,), cardinality='one'),),
    ai_context=ms.ai_context(business_definition='A governed {event} occurrence.'))
def {event}(rows):
    return ms.bind(kind, rows) == {event!r}
"""
    if ordered:
        sequences = ", ".join(
            f'ms.event_sequence({event}, seq, order="integer")' for event in TRIGGERS
        )
        code += f"order = ms.business_order(name='order', subject=subjects, sequences=({sequences},), ai_context=ms.ai_context(business_definition='Per Subject business sequence.'))\n"
    code += f"""open_state = ms.lifecycle_state(name='open', initial=True)
paid_state = ms.lifecycle_state(name='paid')
done_state = ms.lifecycle_state(name='done', terminal={not cycle!r})
model = ms.state_model(name='model', subject=subjects, states=(open_state, paid_state, done_state),
    transitions=(ms.inception(on=started),
      ms.transition(from_state=open_state, on=pulse, to_state=open_state),
      ms.transition(from_state=open_state, on=paid, to_state=paid_state),
      ms.transition(from_state=open_state, on=finished, to_state=done_state),
      ms.transition(from_state=paid_state, on=finished, to_state=done_state),
      {"ms.transition(from_state=done_state, on=finished, to_state=open_state)," if cycle else ""}),
    {"business_order=order," if ordered else ""}
    ai_context=ms.ai_context(business_definition='Canonical business lifecycle.'))
"""
    if observations:
        code += "amount = ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all(), unit='USD')\nrevenue = ms.aggregate(name='revenue', measure=amount, agg='sum', time=instant, nulls=ms.nulls.ignore(), empty=ms.empty.zero())\nfact_count = ms.count(name='fact_count', entity=facts, time=instant)\n"
    (models / "semantic" / "commerce" / "objects.py").write_text(code)
    ms.load(workspace_dir=root)
    store = SessionStore._graph_store(root)
    session_record = store.create_session("r75", report_timezone_name=zone)
    runtime = DatasetRuntime(store, session_record.session_ref)
    session = Session._from_runtime(runtime)
    population = session.members(ms.ref.entity("commerce.subjects"))
    window = mv.time_scope(start=START.isoformat(), end=END.isoformat())
    claims = (
        mv.SourceOriginCompletenessDeclarationV1(
            inputs=tuple(ms.ref.event("commerce." + event) for event in TRIGGERS),
            source_origin_ref=ms.ref.datasource("warehouse"),
            complete_through=END,
            rationale="Fixture source-origin authority.",
        ),
    )
    return session, population, window, claims, values
