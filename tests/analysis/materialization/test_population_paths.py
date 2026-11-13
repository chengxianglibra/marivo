"""Versioned path occurrences and target identity existence semantics."""

import duckdb
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.semantic.errors import SemanticExpressionError
from tests.shared_fixtures import DslCaseFactory, catalog_value


def test_path_anchors_each_version_and_preserves_dangling_terminal_identity(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    domain = case.names.domain
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("CREATE TABLE starts(id BIGINT, day DATE, next BIGINT)")
        db.execute("INSERT INTO starts VALUES (1, '2026-08-01', 10), (1, '2026-09-01', 20)")
        db.execute("CREATE TABLE mids(id BIGINT, day DATE, next BIGINT)")
        db.execute("INSERT INTO mids VALUES (20, '2026-08-01', 99), (20, '2026-09-01', 40)")
        db.execute("CREATE TABLE ends(id BIGINT, day DATE, label VARCHAR)")
        db.execute(
            "INSERT INTO ends VALUES (40, '2026-08-01', 'before'), (40, '2026-09-01', 'after')"
        )
    declarations = ["import marivo.datasource as md\nimport marivo.semantic as ms\n"]
    for entity in ("starts", "mids", "ends"):
        declarations.append(
            f"{entity} = ms.entity(name='{entity}', datasource=ms.ref.datasource('warehouse'), "
            f"source=md.table('{entity}'), primary_key=['id'], "
            f"versioning=ms.snapshot(partition_field=ms.ref.time_dimension('{domain}.{entity}.day'), timezone='UTC'))\n"
            f"{entity}_day = ms.time_dimension_column(name='day', entity={entity}, column='day')\n"
            f"{entity}_id = ms.dimension_column(name='id', entity={entity}, column='id')\n"
        )
    declarations.append(
        "starts_next = ms.dimension_column(name='next', entity=starts, column='next')\n"
        "mids_next = ms.dimension_column(name='next', entity=mids, column='next')\n"
        "label = ms.dimension_column(name='label', entity=ends, column='label')\n"
        "to_mid = ms.relationship(name='to_mid', from_entity=starts, to_entity=mids, keys=[ms.join_on(starts_next, mids_id)])\n"
        "to_end = ms.relationship(name='to_end', from_entity=mids, to_entity=ends, keys=[ms.join_on(mids_next, ends_id)])\n"
    )
    (case.root / "models" / "semantic" / domain / "paths.py").write_text("".join(declarations))
    session = mv.session.get_or_create("anchored-paths", timezone="UTC")
    start = catalog_value(session.catalog, f"{domain}.starts", kind="entity")
    end = catalog_value(session.catalog, f"{domain}.ends", kind="entity")
    to_mid = catalog_value(session.catalog, f"{domain}.to_mid", kind="relationship")
    to_end = catalog_value(session.catalog, f"{domain}.to_end", kind="relationship")
    label = catalog_value(session.catalog, f"{domain}.ends.label", kind="dimension")
    early = session.instant("2026-08-01")
    late = session.instant("2026-09-01")
    members = session.members(start, at=early)
    unanchored = mv.path(to_mid, to_end, source_at=late)
    with pytest.raises(AnalysisError, match="versioned node 1") as missing_image_anchor:
        members.image(via=unanchored)
    assert "marivo.help('analysis.population.image')" in str(missing_image_anchor.value)
    with pytest.raises(AnalysisError, match="versioned node 1") as missing_preimage_anchor:
        members.preimage(session.members(end, at=early), via=unanchored)
    assert "marivo.help('analysis.population.preimage')" in str(missing_preimage_anchor.value)
    historical = mv.path(to_mid.at(early), to_end, source_at=late)
    assert historical.source.id == start.id
    assert historical.target.id == end.id
    assert historical.source_at == late
    assert historical.steps[0].target_at == early
    assert historical.steps[-1].target_at is None
    assert session.frame(members.image(via=historical)).compute().to_pandas().iloc[
        :, 0
    ].tolist() == [99]
    assert (
        session.frame(members.preimage(session.members(end, at=early), via=historical))
        .compute()
        .to_pandas()
        .empty
    )

    current = mv.path(to_mid.at(late), to_end, source_at=late)
    assert session.frame(members.image(via=current)).compute().to_pandas().iloc[:, 0].tolist() == [
        40
    ]
    split = members.image(via=mv.path(to_mid, source_at=late)).image(
        via=mv.path(to_end, source_at=late)
    )
    assert session.frame(split).compute().to_pandas().iloc[:, 0].tolist() == [40]
    requested = mv.coordinates(by=(mv.identity(start),), keys=(((1,),), ((2,),)))
    assert session.frame(requested.image(via=current)).compute().to_pandas().iloc[
        :, 0
    ].tolist() == [40]

    field_path = mv.path(to_mid.at(late), to_end.at(early), source_at=late)
    with pytest.raises(AnalysisError, match="terminal anchor"):
        members.image(via=field_path)
    with pytest.raises(SemanticExpressionError, match="Two time authorities"):
        label.at(early).via(field_path)
    read = session.read(members, {"label": label.via(field_path)}).compute().to_pandas()
    assert read["label"].tolist() == ["before"]
