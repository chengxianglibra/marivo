"""Isolated public R7.4 source fixtures shared with process acceptance workers."""

from datetime import UTC, datetime, timedelta

START = datetime(2026, 2, 1, tzinfo=UTC)
THROUGH = START + timedelta(days=2)


def build_funnel_public(tmp_path, *, form="table", history=None):
    import duckdb

    import marivo.analysis as mv
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.graph_relation import Relation
    from marivo.analysis.materialization.store import SessionStore
    from marivo.analysis.public_dsl import new_members
    from marivo.analysis.session.core import Session
    from marivo.datasource.authoring import DuckDBSpec
    from marivo.datasource.store import save_one
    from marivo.refs import ref
    from marivo.semantic._compiled_state import build_compiled_state
    from marivo.semantic.catalog import SemanticCatalog
    from marivo.semantic.reader import SemanticProject
    from tests.lazy_event_fixtures import make_event_registry
    from tests.lazy_event_runtime_fixtures import setup_event

    source = tmp_path / "source"
    source.mkdir()
    _, _, database = setup_event(source)
    with duckdb.connect(str(database)) as connection:
        connection.execute("UPDATE orders SET day=DATE '2026-02-01' WHERE day IS NULL")
        connection.execute(
            "INSERT INTO started_rows VALUES (201,1,'2026-01-29'), (202,2,'2026-01-29 12:00:00')"
        )
        connection.execute("INSERT INTO finished_rows VALUES (211,2,'2026-01-29 13:00:00')")
    registry, sidecar = make_event_registry(database)
    from dataclasses import replace

    if history is not None:
        from marivo.semantic.ir import JoinKey, RelationshipIR

        entity = "sales.snapshots" if history == "snapshot" else "sales.validity"
        axis_ref = ref.dimension(entity + ".region")
        base = registry.dimensions["sales.customers.region"]
        dimension = replace(
            base, semantic_id=axis_ref.path, entity=entity, name="region", python_symbol="region"
        )
        relationship = RelationshipIR(
            "sales.customer_history",
            "sales",
            "customer_history",
            "sales.customers",
            entity,
            (JoinKey("sales.customers.id", entity + ".id"),),
            base.ai_context,
            base.location,
        )
        registry = replace(
            registry,
            dimensions={**registry.dimensions, axis_ref.path: dimension},
            relationships={**registry.relationships, relationship.semantic_id: relationship},
        )
        sidecar = replace(
            sidecar,
            bodies={
                **sidecar.bodies,
                axis_ref: sidecar.bodies[ref.dimension("sales.customers.region")],
            },
            field_owners={**sidecar.field_owners, axis_ref: ref.entity(entity)},
            catalog_refs=frozenset(
                (*sidecar.catalog_refs, axis_ref, ref.relationship(relationship.semantic_id))
            ),
        )
        with duckdb.connect(str(database)) as connection:
            table = "snapshots" if history == "snapshot" else "validity"
            connection.execute(f"DELETE FROM {table}")
            if history == "snapshot":
                connection.executemany(
                    "INSERT INTO snapshots (id, region, day) VALUES (?, ?, ?)",
                    [
                        (1, "old", "2026-01-29"),
                        (2, "Other", "2026-01-29"),
                        (1, "new", "2026-02-01"),
                        (2, None, "2026-02-01"),
                    ],
                )
            else:
                connection.executemany(
                    'INSERT INTO validity (id, region, start, "end") VALUES (?, ?, ?, ?)',
                    [
                        (1, "old", "2026-01-01", "2026-02-01"),
                        (2, "Other", "2026-01-01", "2026-02-01"),
                        (1, "new", "2026-02-01", None),
                        (2, None, "2026-02-01", None),
                    ],
                )
    if form == "parquet":
        import pyarrow.parquet as pq

        from marivo.datasource.ir import ParquetSourceIR, TableSourceIR

        with duckdb.connect(str(database)) as connection:
            for entity in registry.entities.values():
                if isinstance(entity.source, TableSourceIR):
                    pq.write_table(
                        connection.table(entity.source.table).to_arrow_table(),
                        source / (entity.name + ".parquet"),
                    )
        registry = replace(
            registry,
            entities={
                path: replace(
                    entity,
                    source=ParquetSourceIR(
                        str(source / (entity.name + ".parquet")),
                        columns=tuple(name for name, _ in entity.source.columns),
                    ),
                )
                if isinstance(entity.source, TableSourceIR)
                else entity
                for path, entity in registry.entities.items()
            },
        )
    registry.freeze()
    store = SessionStore._graph_store(tmp_path / "graph")
    store.project_root.mkdir(exist_ok=True)
    (store.project_root / "marivo.toml").write_text('[project]\nname="r74"\n')
    save_one(DuckDBSpec(name="warehouse", path=str(database)), store.project_root)
    runtime = DatasetRuntime(store, store.create_session("r74").session_ref)
    session = Session._from_runtime(runtime)
    project = SemanticProject(workspace_dir=store.project_root)
    project._compiled_state = build_compiled_state(
        registry=registry, sidecar=sidecar, selected_root_roles=("project",), filtered_domains=()
    )
    project._status, project._registry, project._expression_sidecar = "ready", registry, sidecar
    session._catalog_value = SemanticCatalog(project)
    session._sources_value = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    population = new_members(
        Relation.members(runtime, registry, sidecar, "UTC", ref.entity("sales.customers")), runtime
    )
    from marivo.semantic.event import participant_role

    pattern = mv.sequence(
        mv.step(
            participant=participant_role(event=ref.event("sales.started"), name="buyer"),
            key="start",
        ),
        mv.step(
            participant=participant_role(event=ref.event("sales.finished"), name="buyer"),
            key="finish",
        ),
    )
    completeness = (
        mv.BoundedCompletenessDeclarationV1(
            inputs=(ref.event("sales.started"), ref.event("sales.finished")),
            complete_from=START - timedelta(days=3),
            complete_through=THROUGH,
            rationale="Complete fixture period.",
        ),
    )

    def journeys(start=START):
        return session.events.match(
            pattern,
            population=population,
            cohort_window=mv.time_scope(
                start=start.isoformat(), end=(start + timedelta(days=1)).isoformat()
            ),
            completion_through=start + timedelta(days=2),
            matching=mv.first_per_subject(),
            completeness=completeness,
        )

    return session, journeys, pattern, database
