"""History consumer builders shared with isolated A10 and qualification workers."""

from tests.analysis.lifecycle.lifecycle_fixtures import build_lifecycle_public


def build_history_public(root, **options):
    return build_lifecycle_public(root, observations=True, **options)


def selected(history, kind):
    import marivo.analysis as mv
    import marivo.semantic as ms
    from tests.analysis.lifecycle.lifecycle_fixtures import END

    if kind == "state":
        relation = history.read(
            mv.in_state(
                ms.model_state(model=ms.ref.state_model("commerce.model"), name="done"), at=END
            )
        )
        return relation.where(relation.value.eq(True)).members()
    relation = history.intervals() if kind == "interval" else history.violations()
    predicate = (
        relation.state.value.eq("done")
        if kind == "interval"
        else relation.kind.value.eq("transition_from_terminal")
    )
    return relation.where(predicate).members(through=relation.subjects())


def observe(history, kind="state", metric="revenue"):
    import marivo.semantic as ms
    from marivo._temporal import time_scope
    from tests.analysis.lifecycle.lifecycle_fixtures import END, START

    return selected(history, kind).observe(
        ms.ref.metric("commerce." + metric),
        during=time_scope(start=START.isoformat(), end=END.isoformat()),
        via=ms.ref.relationship("commerce.participant"),
    )


def historical_history(root, form, history_kind):
    from dataclasses import replace
    from datetime import timedelta

    import marivo.analysis as mv
    from marivo.refs import ref
    from marivo.semantic._compiled_state import build_compiled_state
    from marivo.semantic.catalog import SemanticCatalog
    from marivo.semantic.reader import SemanticProject
    from tests.analysis.journey.funnel_fixtures import START, THROUGH, build_funnel_public
    from tests.semantic.state_model_context import lifecycle_registry

    session, journeys, _, database = build_funnel_public(root, form=form, history=history_kind)
    import duckdb
    import pyarrow.parquet as pq

    table_name = "snapshots" if history_kind == "snapshot" else "validity"
    with duckdb.connect(str(database)) as connection:
        if history_kind == "snapshot":
            connection.execute(
                "INSERT INTO snapshots (id, region, day) VALUES (3,'silent','2026-01-29'), (3,'silent','2026-02-01'), (4,'silent','2026-01-29'), (4,'silent','2026-02-01')"
            )
        else:
            connection.execute(
                "INSERT INTO validity (id, region, start, \"end\") VALUES (3,'silent','2026-01-01',NULL), (4,'silent','2026-01-01',NULL)"
            )
        if form == "parquet":
            pq.write_table(
                connection.table(table_name).to_arrow_table(),
                root / "source" / (table_name + ".parquet"),
            )
    graph = journeys()._node.binding.graph
    model_registry, _ = lifecycle_registry(database)
    registry = replace(graph.registry, state_models=model_registry.state_models)
    registry.freeze()
    sidecar = replace(
        graph.expression_sidecar,
        catalog_refs=graph.expression_sidecar.catalog_refs | {ref.state_model("sales.purchase")},
    )
    project = SemanticProject(workspace_dir=session._runtime.store.project_root)
    project._compiled_state = build_compiled_state(
        registry=registry, sidecar=sidecar, selected_root_roles=("project",), filtered_domains=()
    )
    project._status, project._registry, project._expression_sidecar = "ready", registry, sidecar
    session._catalog_value = SemanticCatalog(project)
    session._sources_value = session._runtime.sources(semantic_registry=registry, sidecar=sidecar)
    population = session.members(ref.entity("sales.customers"))
    history = session.lifecycle.replay(
        ref.state_model("sales.purchase"),
        population=population,
        window=mv.time_scope(
            start=(START - timedelta(days=3)).isoformat(), end=THROUGH.isoformat()
        ),
        seed=mv.from_inception(),
        completeness=(
            mv.SourceOriginCompletenessDeclarationV1(
                inputs=(ref.event("sales.started"), ref.event("sales.finished")),
                source_origin_ref=ref.datasource("warehouse"),
                complete_through=THROUGH,
                rationale="Complete historical fixture origin.",
            ),
        ),
    )
    entity = "sales.snapshots" if history_kind == "snapshot" else "sales.validity"
    return history, (START - timedelta(days=3), START), ref.dimension(entity + ".region")
