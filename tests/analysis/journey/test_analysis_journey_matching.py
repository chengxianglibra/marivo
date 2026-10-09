"""Independent canonical assignment examples for the matching kernel."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.event import EveryStart, FirstPerSubject
from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline
from marivo.analysis.methods.journey_matching import CoverageWindow, OrderedOccurrence, match
from tests.support.paths import PROJECT_ROOT

START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(seconds=100)


def occurrence(event: str, ordinal: int, *, second: int | None = None) -> OrderedOccurrence:
    return OrderedOccurrence(
        event,
        (ordinal,),
        ("u1",),
        START + timedelta(seconds=ordinal if second is None else second),
        ordinal,
    )


def test_exclusive_and_shared_use_one_canonical_intermediate() -> None:
    rows = tuple(occurrence(event, i) for i, event in enumerate(("a", "a", "b", "c", "c")))
    for assignment, expected in (
        ("shared", ((0, 2, 3), (1, 2, 3))),
        ("exclusive", ((0, 2, 3), (1, 2, 4))),
    ):
        policy = EveryStart(
            completion_assignment="shared" if assignment == "shared" else "exclusive"
        )
        result = match(
            rows,
            events=("a", "b", "c"),
            policy=policy,
            cohort_start=START,
            cohort_end=END,
            completion_through=END,
            coverage=(),
        )
        assert (
            tuple(tuple(step.ordinal for step in row.steps if step) for row in result) == expected
        )
        assert all(row.reach == ("reached",) * 3 for row in result)


@pytest.mark.parametrize(
    "complete, expected", [(frozenset(), "unknown"), (frozenset({"b"}), "unreachable")]
)
def test_missing_middle_propagates_its_truth(complete: frozenset[str], expected: str) -> None:
    result = match(
        (occurrence("a", 0), occurrence("c", 2)),
        events=("a", "b", "c"),
        policy=FirstPerSubject(),
        cohort_start=START,
        cohort_end=END,
        completion_through=END,
        coverage=tuple(CoverageWindow(event, START, END) for event in complete),
    )
    assert result[0].reach == ("reached", expected, expected)
    assert result[0].steps[1:] == (None, None)


def test_repeated_event_cannot_reuse_occurrence_and_first_start_is_fixed() -> None:
    result = match(
        tuple(occurrence("a", i) for i in range(3)),
        events=("a", "a", "a"),
        policy=FirstPerSubject(),
        cohort_start=START,
        cohort_end=END,
        completion_through=END,
        coverage=(CoverageWindow("a", START, END),),
    )
    assert len(result) == 1
    assert tuple(step.ordinal for step in result[0].steps if step) == (0, 1, 2)


def test_same_instant_uses_proved_ordinal_not_input_order() -> None:
    a, b = occurrence("a", 0, second=0), occurrence("b", 1, second=0)
    result = match(
        (b, a),
        events=("a", "b"),
        policy=FirstPerSubject(),
        cohort_start=START,
        cohort_end=END,
        completion_through=END,
        coverage=(),
    )
    assert result[0].steps == (a, b)


def test_no_start_does_not_invent_failed_journey() -> None:
    assert (
        match(
            (occurrence("b", 1),),
            events=("a", "b"),
            policy=FirstPerSubject(),
            cohort_start=START,
            cohort_end=END,
            completion_through=END,
            coverage=(CoverageWindow("a", START, END), CoverageWindow("b", START, END)),
        )
        == ()
    )


def test_cohort_end_excludes_start_but_allows_later_completion() -> None:
    result = match(
        (occurrence("a", 0), occurrence("a", 1), occurrence("b", 2)),
        events=("a", "b"),
        policy=EveryStart(completion_assignment="shared"),
        cohort_start=START,
        cohort_end=START + timedelta(seconds=1),
        completion_through=END,
        coverage=(),
    )
    assert len(result) == 1
    assert result[0].steps[-1] == occurrence("b", 2)


def test_deadline_is_inherited() -> None:
    token = CURRENT.set(ExecuteDeadline(0, clock=lambda: 601))
    try:
        with pytest.raises(DomainPreparationError, match="execute_timeout"):
            match(
                (occurrence("a", 0),),
                events=("a",),
                policy=FirstPerSubject(),
                cohort_start=START,
                cohort_end=END,
                completion_through=END,
                coverage=(),
            )
    finally:
        CURRENT.reset(token)


@pytest.mark.parametrize(
    "rows",
    [
        (occurrence("a", 0), occurrence("a", 0)),
        (occurrence("a", 0), occurrence("b", 0)),
        (occurrence("a", 0, second=2), occurrence("b", 1, second=1)),
        (occurrence("a", 100),),
        (replace(occurrence("a", 0), key=()),),
        (replace(occurrence("a", 0), subject=()),),
    ],
)
def test_invalid_captures_are_rejected(rows: tuple[OrderedOccurrence, ...]) -> None:
    with pytest.raises(DomainPreparationError):
        match(
            rows,
            events=("a", "b"),
            policy=FirstPerSubject(),
            cohort_start=START,
            cohort_end=END,
            completion_through=END,
            coverage=(),
        )


def test_final_reservation_does_not_reserve_intermediate_use() -> None:
    rows = tuple(occurrence(event, i) for i, event in enumerate(("a", "a", "b", "b", "b")))
    result = match(
        rows,
        events=("a", "b", "b"),
        policy=EveryStart(completion_assignment="exclusive"),
        cohort_start=START,
        cohort_end=END,
        completion_through=END,
        coverage=(CoverageWindow("a", START, END), CoverageWindow("b", START, END)),
    )
    assert tuple(tuple(step.ordinal for step in row.steps if step) for row in result) == (
        (0, 2, 3),
        (1, 2, 4),
    )


def test_coverage_is_checked_from_actual_predecessor() -> None:
    result = match(
        (occurrence("a", 0), occurrence("b", 2)),
        events=("a", "b", "c"),
        policy=FirstPerSubject(),
        cohort_start=START,
        cohort_end=END,
        completion_through=END,
        coverage=(CoverageWindow("c", START + timedelta(seconds=1), END),),
    )
    assert result[0].reach == ("reached", "reached", "unreachable")


def test_coverage_start_gap_keeps_absence_unknown() -> None:
    result = match(
        (occurrence("a", 0),),
        events=("a", "b"),
        policy=FirstPerSubject(),
        cohort_start=START,
        cohort_end=END,
        completion_through=END,
        coverage=(CoverageWindow("b", START + timedelta(seconds=1), END),),
    )
    assert result[0].reach == ("reached", "unknown")


@pytest.mark.parametrize(
    "policy",
    [
        FirstPerSubject(),
        EveryStart(completion_assignment="shared"),
        EveryStart(completion_assignment="exclusive"),
    ],
)
@pytest.mark.parametrize("covered", [False, True])
def test_assignments_against_exhaustive_subsequence_oracle(policy, covered):
    from itertools import combinations, product

    for labels in product(("a", "b"), repeat=5):
        for pattern in (("a",), ("a", "b"), ("a", "a", "b"), ("a", "b", "a")):
            # Enumerate all valid subsequences independently of the production scan.
            rows = tuple(
                occurrence(label, i, second=i // 2)
                for i, label in enumerate(labels)
                if label in pattern
            )
            starts = [
                row.ordinal
                for row in rows
                if row.event == pattern[0] and row.instant < START + timedelta(seconds=2)
            ]
            if isinstance(policy, FirstPerSubject):
                starts = starts[:1]
            expected = []
            reserved = set()
            for start in starts:
                prefixes = [(start,)]
                for width in range(2, len(pattern) + 1):
                    for suffix in combinations(range(start + 1, len(labels)), width - 1):
                        candidate = (start, *suffix)
                        if tuple(labels[i] for i in candidate) != pattern[:width]:
                            continue
                        if (
                            width == len(pattern)
                            and isinstance(policy, EveryStart)
                            and policy.completion_assignment == "exclusive"
                            and candidate[-1] in reserved
                        ):
                            continue
                        prefixes.append(candidate)
                longest = max(map(len, prefixes))
                chosen = min(p for p in prefixes if len(p) == longest)
                if len(chosen) == len(pattern) and len(pattern) > 1:
                    reserved.add(chosen[-1])
                expected.append(
                    (
                        chosen + (None,) * (len(pattern) - len(chosen)),
                        ("reached",) * len(chosen)
                        + (("unreachable" if covered else "unknown"),)
                        * (len(pattern) - len(chosen)),
                    )
                )
            actual = match(
                tuple(reversed(rows)),
                events=pattern,
                policy=policy,
                cohort_start=START,
                cohort_end=START + timedelta(seconds=2),
                completion_through=END,
                coverage=tuple(CoverageWindow(event, START, END) for event in sorted(set(pattern)))
                if covered
                else (),
            )
            assert [
                (
                    tuple(None if item is None else item.ordinal for item in journey.steps),
                    journey.reach,
                )
                for journey in actual
            ] == expected


@pytest.mark.runtime
@pytest.mark.parametrize(
    "form,key_type", [("table", "int64"), ("parquet", "int64"), ("parquet", "string")]
)
def test_governed_journey_binding(tmp_path, monkeypatch, form, key_type):
    from marivo._temporal import time_scope
    from marivo.analysis.event import sequence, step
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.graph_relation import Relation
    from marivo.analysis.materialization.store import SessionStore
    from marivo.refs import ref
    from marivo.semantic.event import participant_role
    from tests.semantic.event_fixtures import make_event_registry
    from tests.semantic.source_fixtures import END as COHORT_END
    from tests.semantic.source_fixtures import START as COHORT_START
    from tests.semantic.source_fixtures import THROUGH, seed_event_database

    (tmp_path / "source").mkdir()
    database = seed_event_database(tmp_path / "source")
    import duckdb

    def subject(value: int) -> int | str:
        return str(value) if key_type == "string" else value

    with duckdb.connect(str(database)) as connection:
        if key_type == "string":
            for table, column in (
                ("customers", "id"),
                ("orders", "customer_id"),
                ("started_rows", "customer_id"),
                ("finished_rows", "customer_id"),
                ("started_rows", "occurrence_id"),
                ("finished_rows", "occurrence_id"),
            ):
                connection.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE VARCHAR")
        connection.execute("UPDATE orders SET day = DATE '2026-02-01' WHERE day IS NULL")
    registry, sidecar = make_event_registry(database)
    import pyarrow.parquet as pq

    from marivo.datasource.ir import ParquetSourceIR

    tables = ("customers", "orders", "started_rows", "finished_rows")

    def refresh_files():
        if form == "parquet":
            with duckdb.connect(str(database)) as connection:
                for table in tables:
                    pq.write_table(
                        connection.table(table).to_arrow_table(),
                        tmp_path / "source" / f"{table}.parquet",
                    )

    if form == "parquet":
        registry = replace(
            registry,
            entities={
                path: replace(
                    entity,
                    source=ParquetSourceIR(
                        str(tmp_path / "source" / f"{entity.name}.parquet"),
                        columns=tuple(name for name, _ in entity.source.columns),
                    ),
                )
                if entity.name in tables
                else entity
                for path, entity in registry.entities.items()
            },
        )
    registry.freeze()
    refresh_files()
    store = SessionStore(tmp_path / "graph")
    from marivo.datasource.authoring import DuckDBSpec
    from marivo.datasource.store import save_one

    (tmp_path / "graph" / "marivo.toml").write_text('[project]\nname = "r73"\n')
    save_one(DuckDBSpec(name="warehouse", path=str(database)), tmp_path / "graph")
    session = store.create_session("r73")
    runtime = DatasetRuntime(store, session.session_ref)
    members = Relation.members(runtime, registry, sidecar, "UTC", ref.entity("sales.customers"))
    pattern = sequence(
        step(
            participant=participant_role(event=ref.event("sales.started"), name="buyer"),
            key="start",
        ),
        step(
            participant=participant_role(event=ref.event("sales.finished"), name="buyer"), key="end"
        ),
    )
    from marivo.analysis.public_dsl import new_members
    from marivo.analysis.session.core import Session

    public_session = Session._from_runtime(runtime)
    from marivo.semantic._compiled_state import build_compiled_state
    from marivo.semantic.catalog import SemanticCatalog
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=store.project_root)
    project._compiled_state = build_compiled_state(
        registry=registry,
        sidecar=sidecar,
        selected_root_roles=("project",),
        filtered_domains=(),
    )
    project._status = "ready"
    project._registry = registry
    project._expression_sidecar = sidecar
    public_session._catalog_value = SemanticCatalog(project)
    public_session._sources_value = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    journeys = public_session.events.match(
        pattern,
        population=new_members(members, runtime),
        cohort_window=time_scope(start=COHORT_START.isoformat(), end=COHORT_END.isoformat()),
        completion_through=THROUGH,
        matching=FirstPerSubject(),
        business_order=None,
        completeness=(),
    )

    import marivo.analysis as mv

    # A member projection has no scalar column, even when its source is a float Metric.
    revenue = new_members(members, runtime).observe(
        ref.metric("sales.revenue"),
        during=time_scope(start=COHORT_START.isoformat(), end=THROUGH.isoformat()),
        via=ref.relationship("sales.order_customer"),
        by=(mv.member(),),
    )
    metric_members = revenue.where(revenue.value.is_defined()).members()
    assert metric_members.execute().to_pandas()["member"].tolist() == [
        subject(1),
        subject(2),
        subject(3),
    ]
    selected_journeys = public_session.events.match(
        pattern,
        population=metric_members,
        cohort_window=time_scope(start=COHORT_START.isoformat(), end=COHORT_END.isoformat()),
        completion_through=THROUGH,
        matching=FirstPerSubject(),
    )
    selected_status = (
        selected_journeys.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1])
        .status.execute()
        .to_pandas()
    )
    assert selected_status["value"].tolist() == ["complete", "coverage_censored"]
    selected_again = (
        selected_journeys.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1])
        .completed()
        .duration.members()
    )
    observation_again = selected_again.observe(
        ref.metric("sales.revenue"),
        during=time_scope(start=COHORT_START.isoformat(), end=THROUGH.isoformat()),
        via=ref.relationship("sales.order_customer"),
        by=(mv.member(),),
    )

    from tests.support.documentation import _example

    example = _example("en", "journey-duration")
    namespace = {"session": public_session}
    exec(compile(example, "journey-duration-example", "exec"), namespace)
    assert namespace["revenue"].to_pandas()["member"].tolist() == [subject(2)]
    assert namespace["mean"].to_pandas()["value"].tolist() == [timedelta(hours=3)]

    values = (
        journeys.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1])
        .completed()
        .duration
    )
    selected_source = values.where(values.value.gt(timedelta(0))).members()
    observed = selected_source.observe(
        ref.metric("sales.revenue"),
        during=time_scope(start=COHORT_START.isoformat(), end=COHORT_END.isoformat()),
        via=ref.relationship("sales.order_customer"),
        by=(mv.member(),),
    )
    import marivo.analysis.materialization.journey_execution as local_journey
    import marivo.datasource.adapters as adapters

    assert [action.call for action in observed.contract().actions] == ["relation.execute()"]
    assert (
        "Execute this local result first"
        in dict(observed.contract()._facts)["continuation_boundary"]
    )

    trace = []
    original_read, original_match = adapters.SourceSession.batches, local_journey.execute

    def read(self, *args, **kwargs):
        trace.append("source")
        assert "local" not in trace
        return original_read(self, *args, **kwargs)

    def match_local(*args, **kwargs):
        trace.append("local")
        return original_match(*args, **kwargs)

    with monkeypatch.context() as traced:
        traced.setattr(adapters.SourceSession, "batches", read)
        traced.setattr(local_journey, "execute", match_local)
        observed_result = observed.execute()
        assert observed_result.to_pandas()["member"].tolist() == [subject(1)]
    assert "relation.rollup()" in [a.call for a in observed_result.contract().actions]
    assert observed_result.rollup().execute().to_pandas()["cell_tag"].tolist() == ["null"]
    assert "source" in trace and "local" in trace
    trace.clear()
    with monkeypatch.context() as traced:
        traced.setattr(adapters.SourceSession, "batches", read)
        traced.setattr(local_journey, "execute", match_local)
        observed_again = observation_again.execute().to_pandas()
    assert "source" in trace and "local" in trace
    assert observed_again["member"].tolist() == [subject(1)]
    assert observed_again["value"].tolist() == [10.0]

    from marivo.analysis.domains.completeness import BoundedCompletenessDeclarationV1

    covered = public_session.events.match(
        pattern,
        population=new_members(members, runtime),
        cohort_window=time_scope(start=COHORT_START.isoformat(), end=COHORT_END.isoformat()),
        completion_through=THROUGH,
        matching=FirstPerSubject(),
        completeness=(
            BoundedCompletenessDeclarationV1(
                inputs=(pattern.steps[0].event, pattern.steps[1].event),
                complete_from=COHORT_START,
                complete_through=THROUGH,
                rationale="Fixture coverage",
            ),
        ),
    )
    dropout_source = covered.read(mv.dropped_before(step=pattern.steps[1]))
    for expected, predicate in (
        ([subject(2)], dropout_source.value.eq(True)),
        ([], mv.all_of(dropout_source.value.eq(True), dropout_source.value.eq(False))),
    ):
        selected_members = dropout_source.where(predicate).members(
            through=covered.subjects(pattern.steps[0].participant)
        )
        next_observation = selected_members.observe(
            ref.metric("sales.revenue"),
            during=time_scope(start=COHORT_START.isoformat(), end=THROUGH.isoformat()),
            via=ref.relationship("sales.order_customer"),
            by=(mv.member(),),
        )
        trace.clear()
        with monkeypatch.context() as traced:
            traced.setattr(adapters.SourceSession, "batches", read)
            traced.setattr(local_journey, "execute", match_local)
            result_observation = next_observation.execute()
        assert "source" in trace and "local" in trace
        assert result_observation.to_pandas()["member"].tolist() == expected
        if expected:
            assert result_observation.rollup().execute().to_pandas()["value"].tolist() == [100.0]
        import json

        retained = result_observation._dataset.verified()
        restriction = json.loads(retained.primary.schema.metadata[b"r7.restriction"])
        assert restriction["original_member_rows"] == 4
        assert restriction["selected_member_rows"] == len(expected)
        assert (
            restriction["scope"] == result_observation._node.root.signature.domain.binding.scope_id
        )
        assert restriction["window"] == [COHORT_START.isoformat(), THROUGH.isoformat()]
        components = next(part.table for part in retained.parts if part.role == "original_state")
        assert components["original_state__sum"].to_pylist() == ([100.0] if expected else [])
        assert components["original_state__non_null_count"].to_pylist() == ([1] if expected else [])
        assert components["original_state__absolute_sum"].to_pylist() == (
            [100.0] if expected else []
        )
        assert next(part.table for part in retained.parts if part.role == "coverage")[
            "coverage__complete"
        ].to_pylist() == ([True] if expected else [])
        empty_observation = selected_members.observe(
            ref.metric("sales.revenue"),
            during=time_scope(start=COHORT_START.isoformat(), end=COHORT_END.isoformat()),
            via=ref.relationship("sales.order_customer"),
            by=(mv.member(),),
        ).execute()
        empty_components = next(
            part.table
            for part in empty_observation._dataset.verified().parts
            if part.role == "original_state"
        )
        assert empty_components["original_state__non_null_count"].to_pylist() == (
            [0] if expected else []
        )
        assert empty_components["original_state__sum"].to_pylist() == ([0.0] if expected else [])
        empty_observation.rollup().execute()

    cohort = new_members(members, runtime).cohort(
        dropout_source.value.eq(True),
        rule=mv.any_instance(),
        through=covered.subjects(pattern.steps[0].participant),
    )

    def assert_cohort_state(
        logical: mv.LogicalAnalysisDomain,
        materialized: mv.MaterializedAnalysisDomain,
        counts: dict[int | str, tuple[int, int, int, bool, int]],
    ) -> None:
        assert materialized._dataset is not None
        retained = materialized._dataset.verified()
        decisions = next(part.table for part in retained.parts if part.role == "cohort_decision")
        columns = (
            "cohort_decision__true_count",
            "cohort_decision__unknown_count",
            "cohort_decision__false_count",
            "cohort_decision__accepted",
            "cohort_decision__opportunity_count",
        )
        assert {
            row["key_0"]: tuple(row[column] for column in columns) for row in decisions.to_pylist()
        } == counts
        from marivo.analysis.core.graph import MethodNode

        root = logical._node.root
        assert isinstance(root, MethodNode)
        obligation = next(
            item
            for item in root.derivation.obligations
            if item.check_id == "source.complete_coverage@v1" and item.fact in root.derivation.pre
        )
        proofs = [
            proof
            for proof in materialized._dataset.artifact.descriptor.completed_checks
            if proof.origin_node == root.identity and proof.check_id == obligation.check_id
        ]
        assert len(proofs) == 1
        proof = proofs[0]
        assert proof.fact == obligation.fact
        assert len(proof.ordered_input_occurrences) == len(obligation.fact.inputs)
        assert proof.producing_run_ref == materialized._dataset.artifact.producing_run_ref
        assert proof.status == "completed" and proof.deadline == "consume"

    cohort_result = cohort.execute()
    assert cohort_result.to_pandas()["member"].tolist() == [subject(2)]
    assert_cohort_state(
        cohort,
        cohort_result,
        {
            subject(1): (0, 0, 1, False, 1),
            subject(2): (1, 0, 0, True, 1),
            subject(3): (0, 0, 0, False, 0),
            subject(4): (0, 0, 0, False, 0),
        },
    )
    if form == "table":
        import pyarrow as pa

        from marivo.analysis.compiler.graph_lowering import LoweredLocal
        from marivo.analysis.compiler.graph_plan import CheckRequirement
        from marivo.analysis.errors import AnalysisError
        from marivo.analysis.materialization import graph_local_execution, graph_preparation
        from marivo.analysis.materialization.graph_exchange import CompletedCheck, ExchangeResult

        finish = graph_local_execution._cohort_stage
        make_completed_check = graph_preparation.CompletedCheck
        recorded: list[CheckRequirement] = []

        def record(
            requirement: CheckRequirement,
            result_digest: str,
            consumers: tuple[CheckRequirement, ...] = (),
        ) -> CompletedCheck:
            if (
                requirement.node_id == cohort._node.root.identity
                and requirement.obligation.check_id == "source.complete_coverage@v1"
            ):
                recorded.append(requirement)
            return make_completed_check(requirement, result_digest, consumers)

        for fault, message in (
            ("missing_opportunity", "missing complete opportunity keys or coverage"),
            ("foreign_subject", "Journey subjects escape the target population"),
        ):

            def damaged(
                method: LoweredLocal,
                target: ExchangeResult,
                inputs: tuple[ExchangeResult, ...],
                binding: str,
                fault: str = fault,
            ) -> ExchangeResult:
                opportunity = inputs[0]
                if fault == "missing_opportunity":
                    opportunity = replace(opportunity, primary=opportunity.primary.slice(1))
                else:
                    parts = []
                    for part in opportunity.parts:
                        if part.role == "subject":
                            table = part.table
                            field = table.schema.field("subject__key_0")
                            column = table["subject__key_0"].to_pylist()
                            column[0] = -1
                            table = table.set_column(
                                table.schema.get_field_index(field.name),
                                field,
                                pa.array(column, type=field.type),
                            )
                            part = replace(part, table=table)
                        parts.append(part)
                    opportunity = replace(opportunity, parts=tuple(parts))
                return finish(method, target, (opportunity, *inputs[1:]), binding)

            before_paths = set(store.project_root.rglob("*.parquet"))
            with store._read() as connection:
                before_artifacts = connection.execute(
                    "SELECT count(*) FROM dataset_artifacts"
                ).fetchone()[0]
            recorded.clear()
            with monkeypatch.context() as injected:
                injected.setattr(graph_local_execution, "_cohort_stage", damaged)
                injected.setattr(graph_preparation, "CompletedCheck", record)
                with pytest.raises(AnalysisError, match=message):
                    cohort.execute()
            assert recorded == []
            assert set(store.project_root.rglob("*.parquet")) == before_paths
            with store._read() as connection:
                assert (
                    connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0]
                    == before_artifacts
                )
            assert store.resources(session.session_ref) == ()
    logical = journeys._node
    elapsed_source = journeys.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1])
    assert elapsed_source.status.execute().to_pandas()["value"].tolist() == [
        "complete",
        "coverage_censored",
    ]
    assert logical.root.signature.domain.kind == "journey"
    result = logical.execute()
    assert len(result.to_pandas()) == 2
    import duckdb

    from marivo.analysis.materialization.graph_storage import read_result
    from marivo.analysis.materialization.journey_execution import ASSIGNMENT

    def reaches(dataset):
        retained = read_result(store.project_root, dataset.artifact.descriptor)
        return tuple(
            ASSIGNMENT.validate_json(value).reach
            for value in next(part.table for part in retained.parts if part.role == "journey")[
                "journey__assignment"
            ].to_pylist()
        )

    assert reaches(result) == (("reached", "reached"), ("reached", "unknown"))
    with duckdb.connect(str(database)) as connection:
        connection.execute("INSERT INTO finished_rows VALUES (992001, 2, '2026-02-02 12:00:00')")
    refresh_files()
    changed = logical.execute()
    assert changed.artifact.artifact_ref != result.artifact.artifact_ref
    assert reaches(changed) == (("reached", "reached"), ("reached", "reached"))
    with duckdb.connect(str(database)) as connection:
        connection.execute("DELETE FROM started_rows")
        connection.execute("DELETE FROM finished_rows")
        connection.execute(
            "INSERT INTO started_rows VALUES (1,1,'2026-02-01 00:00:00'), (2,1,'2026-02-01 00:00:20'), (3,2,'2026-02-01 00:00:00')"
        )
        connection.execute(
            "INSERT INTO finished_rows VALUES (11,1,'2026-02-01 00:00:30'), (12,2,'2026-02-01 00:01:40')"
        )
    refresh_files()
    repeated = public_session.events.match(
        pattern,
        population=new_members(members, runtime),
        cohort_window=time_scope(start=COHORT_START.isoformat(), end=COHORT_END.isoformat()),
        completion_through=THROUGH,
        matching=mv.every_start(completion_assignment="shared"),
    )
    completed_values = (
        repeated.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1])
        .completed()
        .duration
    )
    full_elapsed = repeated.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1])
    complete_status = full_elapsed.status
    full_duration = full_elapsed.duration
    qualified_subjects = new_members(members, runtime).cohort(
        mv.all_of(
            complete_status.value.eq("complete"), full_duration.value.lt(timedelta(seconds=90))
        ),
        rule=mv.any_instance(),
        through=repeated.subjects(pattern.steps[0].participant),
    )
    qualified_result = qualified_subjects.execute()
    assert qualified_result.to_pandas()["member"].tolist() == [subject(1)]
    assert_cohort_state(
        qualified_subjects,
        qualified_result,
        {
            subject(1): (2, 0, 0, True, 2),
            subject(2): (0, 0, 1, False, 1),
            subject(3): (0, 0, 0, False, 0),
            subject(4): (0, 0, 0, False, 0),
        },
    )
    logical_subject_mean = completed_values.group_by(ref.entity("sales.customers")).aggregate(
        mv.mean()
    )
    assert [a.call for a in logical_subject_mean.contract().actions] == ["relation.execute()"]
    subject_mean_result = logical_subject_mean.execute()
    assert "relation.rollup()" in [a.call for a in subject_mean_result.contract().actions]
    assert subject_mean_result.rollup().execute().to_pandas()["value"].iloc[0] == timedelta(
        microseconds=46_666_667
    )
    source_mean = completed_values.aggregate(mv.mean()).execute()
    assert source_mean.to_pandas()["value"].iloc[0] == timedelta(microseconds=46_666_667)
    fixed_values = completed_values.execute()
    mean = fixed_values.aggregate(mv.mean()).execute()
    assert mean.to_pandas()["value"].iloc[0] == timedelta(microseconds=46_666_667)
    from marivo.analysis.errors import AnalysisError

    with pytest.raises(AnalysisError, match="mean reduction only"):
        mean.compare(mean)

    by_subject = fixed_values.group_by(ref.entity("sales.customers")).aggregate(mv.mean()).execute()
    assert sorted(by_subject.to_pandas()["value"].tolist()) == [
        timedelta(seconds=20),
        timedelta(seconds=100),
    ]
    assert sum(by_subject.to_pandas()["value"].tolist(), timedelta(0)) / 2 == timedelta(seconds=60)
    assert by_subject.rollup().execute().to_pandas()["value"].iloc[0] == timedelta(
        microseconds=46_666_667
    )
    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization import graph_storage
    from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline

    def artifact_count():
        with store._connection() as connection:
            return connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0]

    prior_count = artifact_count()

    def failed_write(*args, **kwargs):
        raise OSError("injected journey publication failure")

    with monkeypatch.context() as injected:
        injected.setattr(graph_storage.pq, "write_table", failed_write)
        with pytest.raises(AnalysisError, match="write"):
            repeated.execute()
    assert artifact_count() == prior_count

    def cancelled(*args, **kwargs):
        raise KeyboardInterrupt("injected journey cancellation")

    with monkeypatch.context() as injected:
        injected.setattr(local_journey, "execute", cancelled)
        with pytest.raises(KeyboardInterrupt):
            repeated.execute()
    assert artifact_count() == prior_count
    token = CURRENT.set(ExecuteDeadline(0, clock=lambda: 601))
    try:
        with pytest.raises(AnalysisError, match="execute_timeout"):
            repeated.execute()
        with pytest.raises(AnalysisError, match="execute_timeout"):
            mean.rollup().execute()
    finally:
        CURRENT.reset(token)
    assert artifact_count() == prior_count
    with pytest.raises(AnalysisError):
        repeated.time_to_event(from_step=pattern.steps[1], to_step=pattern.steps[0])
    with pytest.raises(AnalysisError):
        repeated.read(mv.dropped_before(step=pattern.steps[1]))
    with pytest.raises(AnalysisError):
        repeated.subjects(participant_role(event=ref.event("sales.finished"), name="foreign"))
    with pytest.raises(AnalysisError):
        public_session.events.match(
            pattern,
            population=new_members(members, runtime).execute(),
            cohort_window=time_scope(start=COHORT_START.isoformat(), end=COHORT_END.isoformat()),
            completion_through=THROUGH,
            matching=FirstPerSubject(),
        )
    fixed_members = selected_source.execute()
    foreign_store = SessionStore(tmp_path / "foreign")
    foreign_record = foreign_store.create_session("foreign")
    foreign_runtime = DatasetRuntime(foreign_store, foreign_record.session_ref)
    foreign_session = Session._from_runtime(foreign_runtime)
    foreign_session._sources_value = foreign_runtime.sources(
        semantic_registry=registry, sidecar=sidecar
    )
    with monkeypatch.context() as guarded:

        def no_source(*args, **kwargs):
            raise AssertionError("invalid binding must reject before business reads")

        guarded.setattr(adapters.SourceSession, "batches", no_source)
        with pytest.raises(AnalysisError, match="foreign Session population"):
            foreign_session.events.match(
                pattern,
                population=new_members(members, runtime),
                cohort_window=time_scope(
                    start=COHORT_START.isoformat(), end=COHORT_END.isoformat()
                ),
                completion_through=THROUGH,
                matching=FirstPerSubject(),
            )
        assert not hasattr(fixed_members, "observe")
        with pytest.raises(AnalysisError):
            public_session.events.match(
                pattern,
                population=fixed_members,
                cohort_window=time_scope(
                    start=COHORT_START.isoformat(), end=COHORT_END.isoformat()
                ),
                completion_through=THROUGH,
                matching=FirstPerSubject(),
            )
    database.unlink()
    for source_file in (tmp_path / "source").glob("*.parquet"):
        source_file.unlink()
    assert reaches(result) == (("reached", "reached"), ("reached", "unknown"))
    from marivo.analysis.public_dsl import wrap_materialized

    public = wrap_materialized(logical, runtime, result)
    elapsed = public.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1])
    fixed_elapsed = elapsed.execute()
    assert fixed_elapsed.status.execute().to_pandas()["value"].tolist() == [
        "complete",
        "coverage_censored",
    ]
    for relation, tags in (
        (fixed_elapsed.started_at, ["defined", "defined"]),
        (fixed_elapsed.completed_at, ["defined", "undefined"]),
        (fixed_elapsed.duration, ["defined", "undefined"]),
        (fixed_elapsed.observed_duration, ["defined", "unknown"]),
        (fixed_elapsed.followup_until, ["defined", "unknown"]),
    ):
        assert relation.execute().to_pandas()["cell_tag"].tolist() == tags
    complete = fixed_elapsed.completed().execute()
    assert len(complete.to_pandas()) == 1
    assert complete.duration.execute().to_pandas()["cell_tag"].tolist() == ["defined"]
    import marivo.analysis as mv

    assert complete.duration.aggregate(mv.mean()).execute().to_pandas()["cell_tag"].tolist() == [
        "defined"
    ]
    dropout = public.read(mv.dropped_before(step=pattern.steps[1]))
    with pytest.raises(AnalysisError, match="unknown"):
        dropout.where(dropout.value.eq(True)).execute()
    values = complete.duration
    empty_mean = values.where(values.value.lt(timedelta(0))).aggregate(mv.mean()).execute()
    assert empty_mean.to_pandas()["cell_reason"].tolist() == ["empty_completed_set"]
    empty_state = next(
        part.table for part in empty_mean._dataset.verified().parts if part.role == "row_state"
    )
    assert empty_state["row_state__sum"].to_pylist() == [0]
    assert empty_state["row_state__count"].to_pylist() == [0]
    selected = values.where(values.value.gt(timedelta(0)))
    assert len(selected.members().execute().to_pandas()) == 1
    import os
    import subprocess
    import sys

    script = """
import os, sys
from datetime import timedelta
import ibis
import marivo.analysis as mv
from marivo.datasource.adapters import SourceSession
from marivo.analysis.methods import journey_matching
from marivo.analysis.materialization import journey_execution

def forbidden(*args, **kwargs):
    raise AssertionError("fixed Journey continuation must not read or rematch")
SourceSession.bind = forbidden
ibis.duckdb.connect = forbidden
journey_matching.match = forbidden
journey_execution.match = forbidden
journey_execution.execute = forbidden
os.chdir(sys.argv[1])
session = mv.session.resume(sys.argv[2], by="id")
elapsed = session.artifact(sys.argv[3])
assert isinstance(elapsed, mv.MaterializedEventDurationResult)
assert elapsed.status.execute().to_pandas()["value"].tolist() == ["complete", "coverage_censored"]
for relation in (elapsed.started_at, elapsed.completed_at, elapsed.duration, elapsed.observed_duration, elapsed.followup_until):
    assert len(relation.execute().to_pandas()) == 2
completed = session.artifact(sys.argv[4])
assert isinstance(completed, mv.MaterializedCompletedJourneys)
values = completed.duration
assert len(values.execute().to_pandas()) == 1
values.aggregate(mv.mean()).execute()
summary = session.artifact(sys.argv[5])
assert summary.rollup().execute().to_pandas()["value"].iloc[0] == timedelta(microseconds=46666667)
assert "captured_precision" in dict(elapsed.contract()._facts)
"""
    subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(store.project_root),
            public_session.id,
            fixed_elapsed.state.artifact_ref.ref,
            complete.state.artifact_ref.ref,
            mean.state.artifact_ref.ref,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    victim = next(p for p in result.artifact.descriptor.parts if p.role == "journey")
    path = store.project_root / victim.local.project_relative_path / "data.parquet"
    original_bytes = path.read_bytes()
    try:
        path.write_bytes(original_bytes[:-1] + b"!")
        with pytest.raises(AnalysisError):
            public_session.artifact(result.artifact.artifact_ref)
    finally:
        path.write_bytes(original_bytes)


def test_duration_and_dropout_share_assignment_truth() -> None:
    from marivo.analysis.core.model import Defined, Undefined, Unknown
    from marivo.analysis.methods.journey_duration import dropped_before, duration

    for windows, status, reach in (
        ((), "coverage_censored", Unknown("coverage_censored")),
        ((CoverageWindow("b", START, END),), "incomplete", Defined(True)),
    ):
        assignments = match(
            (occurrence("a", 0),),
            events=("a", "b"),
            policy=FirstPerSubject(),
            cohort_start=START,
            cohort_end=END,
            completion_through=END,
            coverage=windows,
        )
        value = duration(
            assignments[0],
            from_step=0,
            to_step=1,
            events=("a", "b"),
            completion_through=END,
            coverage=windows,
        )
        assert value.status == status
        assert value.duration == Undefined("not_completed")
        assert dropped_before(assignments[0], step=1, policy="first_per_subject") == reach
        assert value.observed_duration == (
            Defined(100_000_000) if status == "incomplete" else Unknown("coverage_censored")
        )


def test_duration_unknown_entry_is_distinct_from_absent_entry() -> None:
    from marivo.analysis.methods.journey_duration import duration

    for windows, expected in (
        ((), "entry_unknown"),
        ((CoverageWindow("b", START, END),), "not_entered"),
    ):
        assignment = match(
            (occurrence("a", 0),),
            events=("a", "b", "c"),
            policy=FirstPerSubject(),
            cohort_start=START,
            cohort_end=END,
            completion_through=END,
            coverage=windows,
        )[0]
        value = duration(
            assignment,
            from_step=1,
            to_step=2,
            events=("a", "b", "c"),
            completion_through=END,
            coverage=windows,
        )
        assert value.status == expected


def test_completed_elapsed_and_coverage_prefix() -> None:
    from marivo.analysis.core.model import Defined
    from marivo.analysis.methods.journey_duration import duration

    assignment = match(
        (occurrence("a", 0), occurrence("b", 30)),
        events=("a", "b"),
        policy=FirstPerSubject(),
        cohort_start=START,
        cohort_end=END,
        completion_through=END,
        coverage=(),
    )[0]
    value = duration(
        assignment, from_step=0, to_step=1, events=("a", "b"), completion_through=END, coverage=()
    )
    assert value.status == "complete"
    assert value.duration == value.observed_duration == Defined(30_000_000)
    prefix = (CoverageWindow("b", START, START + timedelta(seconds=10)),)
    assignment = match(
        (occurrence("a", 0),),
        events=("a", "b"),
        policy=FirstPerSubject(),
        cohort_start=START,
        cohort_end=END,
        completion_through=END,
        coverage=prefix,
    )[0]
    value = duration(
        assignment,
        from_step=0,
        to_step=1,
        events=("a", "b"),
        completion_through=END,
        coverage=prefix,
    )
    assert value.status == "coverage_censored"
    assert value.observed_duration == Defined(10_000_000)
    assert value.followup_until == Defined(START + timedelta(seconds=10))


def test_elapsed_ticks_use_instants_across_dst_fold() -> None:
    from zoneinfo import ZoneInfo

    from marivo.analysis.methods.journey_duration import ticks

    zone = ZoneInfo("America/New_York")
    first = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    second = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    assert ticks(first, second) == 3_600_000_000


@pytest.mark.parametrize("marker", ["float64", "boolean", "string", "timestamp"])
def test_preparation_qualification_ignores_projected_scalar_marker(marker):
    from marivo.analysis.methods.builtin import specialize_numeric
    from marivo.analysis.methods.domain_preparation import implementations
    from marivo.analysis.methods.physical import ScalarType, SourceShape
    from marivo.analysis.methods.semantics import MethodKey

    template = next(
        item
        for item in implementations(MethodKey("occurrence.prepare"))
        if isinstance(item.key.shape, SourceShape)
    )
    key = replace(template.key, input_types=(ScalarType(marker),))
    selected = specialize_numeric(template, key)
    assert selected.key == key
    assert selected.parts == template.parts
    assert selected.checks == template.checks
    assert "source.finite_numeric@v1" in selected.checks
