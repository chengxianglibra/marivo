"""Module-owned Anchor projects using the shared governed declarations."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import ibis
import pyarrow as pa
import pyarrow.parquet as pq

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic.event import participant_role
from tests.analysis.lifecycle.lifecycle_fixtures import START, build_lifecycle_public


def build_anchors(root, **kwargs):
    qualified_history = kwargs.pop("qualification_history", False)
    form = kwargs.get("form", "table")
    session, members, window, claims, values = build_lifecycle_public(
        root, observations=True, **kwargs
    )
    if kwargs.get("empty", False):
        values = []
    backend = ibis.duckdb.connect(root / "source.duckdb")
    try:
        data = backend.table("facts").to_pyarrow()
        data = data.append_column(
            "integer", pa.array([2**53 + row[3] for row in values], type=pa.int64())
        )
        data = data.append_column(
            "decimal",
            pa.array([Decimal("1.000001") * row[3] for row in values], type=pa.decimal128(38, 6)),
        )
        data = data.append_column("ticks", pa.array([row[3] for row in values], type=pa.int64()))
        backend.create_table("facts", data, overwrite=True)
        backend.raw_sql("ALTER TABLE facts ALTER ticks TYPE INTERVAL USING to_microseconds(ticks)")
        if form == "parquet":
            pq.write_table(
                data.set_column(
                    data.schema.get_field_index("ticks"),
                    "ticks",
                    data["ticks"].cast(pa.duration("us")),
                ),
                root / "facts.parquet",
            )
        other = data.set_column(
            data.schema.get_field_index("instant"),
            "instant",
            pa.array(
                [point + timedelta(seconds=2) for point in data["instant"].to_pylist()],
                type=data.schema.field("instant").type,
            ),
        )
        backend.create_table("other", other, overwrite=True)
        if form == "parquet":
            pq.write_table(other, root / "other.parquet")
    finally:
        backend.disconnect()
    path = root / "models/semantic/commerce/objects.py"
    code = path.read_text().replace(
        "granularity='second', parse=", "granularity='second', is_default=True, parse="
    )
    for name in ("integer", "decimal", "ticks"):
        code += f"{name} = ms.measure_column(name={name!r}, entity=facts, column={name!r}, additivity=ms.additive_all(), unit='1')\n{name}_sum = ms.aggregate(name={name + '_sum'!r}, measure={name}, agg='sum', time=instant, nulls=ms.nulls.ignore(), empty=ms.empty.zero())\n"
    subject_keys = list(backend_keys(members))
    identity_keys = [name for name in data.column_names if name.startswith("oid")]
    source = (
        "md.table('other')" if form == "table" else f"md.parquet({str(root / 'other.parquet')!r})"
    )
    code += f"other = ms.entity(name='other', datasource=ms.ref.datasource('warehouse'), source={source}, primary_key={identity_keys!r})\n"
    for name in subject_keys:
        code += f"other_{name} = ms.dimension_column(name={'other_' + name!r}, entity=other, column={name!r})\n"
    code += "other_time = ms.time_dimension_column(name='other_time', entity=other, column='instant', granularity='second', is_default=True, parse=ms.timestamp(timezone='UTC'))\n"
    code += f"other_participant = ms.relationship(name='other_participant', from_entity=other, to_entity=subjects, keys=[{', '.join('ms.join_on(other_' + name + ', subject_' + name + ')' for name in subject_keys)}])\n"
    for name, column in (("other_amount", "amount"), ("other_integer", "integer")):
        code += f"{name} = ms.measure_column(name={name!r}, entity=other, column={column!r}, additivity=ms.additive_all(), unit={'USD' if column == 'amount' else '1'!r})\n{name}_sum = ms.aggregate(name={name + '_sum'!r}, measure={name}, agg='sum', time=other_time, nulls=ms.nulls.ignore(), empty=ms.empty.zero())\n"
    if qualified_history:
        history_code = history_chain(root, subject_keys, form=form)
        first_event = code.index("@ms.event(")
        code = code[:first_event] + history_code + code[first_event:]
        code = code.replace(
            "path=(participant,)", "path=(facts_snapshot, snapshot_validity, validity_subject)"
        )
        code += f"other_snapshot = ms.relationship(name='other_snapshot', from_entity=other, to_entity=snapshot_history, keys=[{', '.join('ms.join_on(other_' + name + ', snapshot_' + name + ')' for name in subject_keys)}])\n"
    path.write_text(code)
    ms.load(workspace_dir=root)
    from marivo.analysis.session.core import Session

    session = Session._from_runtime(session._runtime)
    members = session.members(ms.ref.entity("commerce.subjects"))
    return session, members, window, claims, values


def backend_keys(members):
    return [coordinate.field for coordinate in members._node.root.signature.domain.instance_key]


def event_anchors(session, members, window):
    return session.anchors(
        participant_role(event=ms.ref.event("commerce.started"), name="subject"),
        population=members,
        during=window,
        business_order=ms.ref.business_order("commerce.order"),
    )


def journey(session, members, window, claims, *, shared=False, first=False):
    return session.events.match(
        mv.EventPattern(
            steps=(
                mv.step(
                    participant=participant_role(
                        event=ms.ref.event("commerce.started"), name="subject"
                    ),
                    key="start",
                ),
                mv.step(
                    participant=participant_role(
                        event=ms.ref.event("commerce.finished"), name="subject"
                    ),
                    key="finish",
                ),
            ),
        ),
        population=members,
        cohort_window=window,
        completion_through=START + timedelta(seconds=110),
        matching=mv.first_per_subject()
        if first
        else mv.every_start(completion_assignment="shared" if shared else "exclusive"),
        business_order=ms.ref.business_order("commerce.order"),
        completeness=tuple(
            replace(
                claim,
                inputs=tuple(
                    event
                    for event in claim.inputs
                    if event.path in ("commerce.started", "commerce.finished")
                ),
            )
            for claim in claims
        ),
    )


def observations(anchors, *, calendar=False):
    window = (
        mv.calendar_days(1, ZoneInfo("America/New_York"))
        if calendar
        else mv.elapsed(mv.duration(seconds=10))
    )
    captured = next(
        part
        for part in anchors._node.root.signature.parts
        if type(part).__name__ == "AnchorDomainPart"
    )
    history = len(captured.preparation.events[0].path) == 3
    facts_path = (
        (
            ms.ref.relationship("commerce.facts_snapshot"),
            ms.ref.relationship("commerce.snapshot_validity"),
            ms.ref.relationship("commerce.validity_subject"),
        )
        if history
        else (ms.ref.relationship("commerce.participant"),)
    )
    other_path = (
        (ms.ref.relationship("commerce.other_snapshot"), *facts_path[1:])
        if history
        else (ms.ref.relationship("commerce.other_participant"),)
    )
    route = (mv.path(*facts_path),) if history else facts_path[0]
    bases = [
        ms.ref.metric("commerce.fact_count"),
        ms.ref.metric("commerce.integer_sum"),
        ms.ref.metric("commerce.revenue"),
        ms.ref.metric("commerce.decimal_sum"),
        ms.ref.metric("commerce.ticks_sum"),
    ]
    ratios = [
        mv.runtime_metric.ratio(base, base, label="ratio_" + str(i))
        for i, base in enumerate(bases[1:])
    ]
    linear = mv.runtime_metric.linear(
        add=[bases[2], ms.ref.metric("commerce.other_amount_sum")], label="both"
    )
    multi_ratio = mv.runtime_metric.ratio(
        bases[1], ms.ref.metric("commerce.other_integer_sum"), label="both_ratio"
    )
    result = []
    for metric in (*bases, *ratios, linear, multi_ratio):
        via = (
            (
                mv.path(*facts_path),
                mv.path(*other_path),
            )
            if metric in (linear, multi_ratio)
            else route
        )
        result.append(anchors.observe(metric, within=window, via=via))
    return result


def with_history_route(root, session, *, kind, form):
    from datetime import date

    from tests.analysis.lifecycle.lifecycle_fixtures import keys

    first, second, _ = keys("i", "sid", 3)["sid"]
    fields = {"account": [first, first, second, second], "target": [first, second, second, second]}
    data = pa.table(
        {
            **fields,
            "day": pa.array([date(2026, 2, 1), date(2026, 2, 2)] * 2, type=pa.date32()),
            "finish": pa.array([date(2026, 2, 2), None] * 2, type=pa.date32()),
        }
    )
    backend = ibis.duckdb.connect(root / "source.duckdb")
    try:
        backend.create_table("history", data)
        if form == "parquet":
            pq.write_table(data, root / "history.parquet")
    finally:
        backend.disconnect()
    source = (
        "md.table('history')"
        if form == "table"
        else f"md.parquet({str(root / 'history.parquet')!r})"
    )
    version = (
        "ms.snapshot(partition_field=ms.ref.time_dimension('commerce.history.day'), grain='day', timezone='UTC')"
        if kind == "snapshot"
        else "ms.validity(valid_from=ms.ref.time_dimension('commerce.history.day'), valid_to=ms.ref.time_dimension('commerce.history.finish'), interval='closed_open', open_end=(None,))"
    )
    path = root / "models/semantic/commerce/objects.py"
    code = (
        path.read_text()
        + f"history = ms.entity(name='history', datasource=ms.ref.datasource('warehouse'), source={source}, primary_key=['account'], versioning={version})\n"
    )
    code += "history_account = ms.dimension_column(name='account', entity=history, column='account')\nhistory_target = ms.dimension_column(name='target', entity=history, column='target')\n"
    code += "history_day = ms.time_dimension_column(name='day', entity=history, column='day', granularity='day')\nhistory_finish = ms.time_dimension_column(name='finish', entity=history, column='finish', granularity='day')\n"
    code += "facts_history = ms.relationship(name='facts_history', from_entity=facts, to_entity=history, keys=[ms.join_on(fact_sid, history_account)])\nhistory_subject = ms.relationship(name='history_subject', from_entity=history, to_entity=subjects, keys=[ms.join_on(history_target, subject_sid)])\n"
    path.write_text(code)
    ms.load(workspace_dir=root)
    from marivo.analysis.session.core import Session

    updated = Session._from_runtime(session._runtime)
    return updated, updated.members(ms.ref.entity("commerce.subjects"))


def history_chain(root, names, *, form):
    from datetime import date

    backend = ibis.duckdb.connect(root / "source.duckdb")
    try:
        members = backend.table("subjects").to_pyarrow().to_pydict()
        fields = {
            name: [value for value in values for _ in range(2)] for name, values in members.items()
        }
        dates = [date(2026, 1, 31), date(2026, 2, 1)] * len(next(iter(members.values())))
        for kind in ("snapshot", "validity"):
            data = pa.table({**fields, "day": pa.array(dates, type=pa.date32())})
            if kind == "validity":
                data = data.append_column(
                    "finish",
                    pa.array([date(2026, 2, 1), None] * (len(dates) // 2), type=pa.date32()),
                )
            backend.create_table(kind + "_history", data)
            if form == "parquet":
                pq.write_table(data, root / (kind + "_history.parquet"))
    finally:
        backend.disconnect()
    code = ""
    for kind in ("snapshot", "validity"):
        name = kind + "_history"
        source = (
            f"md.table({name!r})"
            if form == "table"
            else f"md.parquet({str(root / (name + '.parquet'))!r})"
        )
        version = (
            "ms.snapshot(partition_field=ms.ref.time_dimension('commerce.snapshot_history.day'), grain='day', timezone='UTC')"
            if kind == "snapshot"
            else "ms.validity(valid_from=ms.ref.time_dimension('commerce.validity_history.day'), valid_to=ms.ref.time_dimension('commerce.validity_history.finish'), interval='closed_open', open_end=(None,))"
        )
        code += f"{name} = ms.entity(name={name!r}, datasource=ms.ref.datasource('warehouse'), source={source}, primary_key={names!r}, versioning={version})\n"
        for field in names:
            code += f"{kind}_{field} = ms.dimension_column(name={field!r}, entity={name}, column={field!r})\n"
        for field in ("day", "finish") if kind == "validity" else ("day",):
            code += f"{kind}_{field} = ms.time_dimension_column(name={field!r}, entity={name}, column={field!r}, granularity='day')\n"
    for name, source, target, left, right in (
        ("facts_snapshot", "facts", "snapshot_history", "fact", "snapshot"),
        ("snapshot_validity", "snapshot_history", "validity_history", "snapshot", "validity"),
        ("validity_subject", "validity_history", "subjects", "validity", "subject"),
    ):
        joins = ", ".join(f"ms.join_on({left}_{field}, {right}_{field})" for field in names)
        code += f"{name} = ms.relationship(name={name!r}, from_entity={source}, to_entity={target}, keys=[{joins}])\n"
    return code
