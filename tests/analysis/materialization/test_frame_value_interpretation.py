"""Current value explanations use logical intent and exact captured endpoint mappings."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.core.data_schema import DataAxis, DataColumn, DataSchema
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.core.frame_data import CompletedFrameData, CompletedFrameResult
from marivo.analysis.core.frame_interpretation import (
    FrameAlignmentAxis,
    FrameAxisGroup,
    FrameDerivedValueInput,
    FrameFixedGroup,
    FrameNamedValueInput,
    FrameObservationContext,
    FramePairStatisticContext,
    FrameShareContext,
    FrameStatisticContext,
    validate_current_facts,
)
from marivo.analysis.core.frame_metadata import EvaluationRecord, FrameResultMetadata
from marivo.analysis.core.lineage import ColumnLineage, SemanticObjectIdentity, directory
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.frame_services import LocalFrameServices
from tests.shared_fixtures import DslCaseFactory, catalog_value

INT64 = ScalarDataType("int64")
A = SemanticObjectIdentity("metric", "test.a", "a")
B = SemanticObjectIdentity("metric", "test.b", "b")
C = SemanticObjectIdentity("metric", "test.c", "c")


@pytest.fixture
def local(tmp_path: Path) -> LocalFrameServices:
    return DatasetRuntime.create(tmp_path, "value-interpretation", timezone="UTC").local


def _frame(
    local: LocalFrameServices,
    rows: tuple[tuple[int, int, int | None, int | None], ...] = ((0, 0, 1, 2), (0, 1, 3, 6)),
) -> AnalysisFrame:
    schema = DataSchema(
        (
            DataAxis("group", (DataColumn("g", "group", INT64, False),), name="group"),
            DataAxis("sample", (DataColumn("i", "sample", INT64, False),), name="sample"),
        ),
        (
            DataColumn("x", "input.x", INT64, True, "USD"),
            DataColumn("y", "input.y", INT64, True, "USD"),
        ),
        value_paths=(("input", "x"), ("input", "y")),
        value_contexts=(("input.x", FrameObservationContext(A, None, None)),),
    )
    lineage = directory(
        schema,
        (ColumnLineage((A, B)), ColumnLineage((C,))),
        (ColumnLineage(), ColumnLineage()),
        (),
        (),
    )
    metadata = FrameResultMetadata(
        "interpretation-input",
        EvaluationRecord(datetime(2026, 10, 30, tzinfo=timezone.utc), (), ()),
        lineage=lineage,
    )
    data = CompletedFrameData(schema, pd.DataFrame(rows, columns=("g", "i", "x", "y")))
    return AnalysisFrame._from_memory(local, CompletedFrameResult(data, metadata))


def test_explicit_share_keeps_intent_separate_from_equivalent_quotient(
    local: LocalFrameServices,
) -> None:
    frame = _frame(local)
    x = frame["input.x"]
    output = frame.select(
        {
            "share": x.share(along=(frame.axes[1],)),
            "quotient": x / frame.over(x.sum(), by=(frame.axes[0],)),
            "arithmetic": x.share(along=frame.axes) * 1,
        }
    )
    context = output.schema.value_contexts["share"]
    assert context == FrameShareContext(
        FrameNamedValueInput("input.x", A), (FrameAxisGroup("group"),), (FrameAxisGroup("sample"),)
    )
    assert tuple(output.schema.value_contexts) == ("share",)
    completed = output.compute()
    assert completed.schema.value_contexts == output.schema.value_contexts
    assert completed.to_pandas()["share"].tolist() == [Decimal("0.25"), Decimal("0.75")]
    assert completed.to_pandas()["quotient"].tolist() == [Decimal("0.25"), Decimal("0.75")]
    assert output.lineage.values["share"] == output.lineage.values["quotient"]


def test_share_filter_timing_signed_values_and_zero_sum(local: LocalFrameServices) -> None:
    frame = _frame(local)
    shares = frame.select({"share": frame["input.x"].share(along=frame.axes)})
    after = shares.where(shares["share"] > Decimal("0.5")).compute()
    filtered = frame.where(frame["input.x"] > 1)
    before = filtered.select({"share": filtered["input.x"].share(along=filtered.axes)}).compute()
    assert after.to_pandas()["share"].tolist() == [Decimal("0.75")]
    assert before.to_pandas()["share"].tolist() == [Decimal("1")]
    assert after.schema.value_contexts["share"].row_scope == "restricted"
    assert before.schema.value_contexts["share"].row_scope == "complete"
    signed = _frame(local, ((0, 0, 2, 1), (0, 1, -1, 1)))
    result = signed.select({"s": signed["input.x"].share(along=signed.axes)}).compute()
    assert result.to_pandas()["s"].tolist() == [Decimal("2"), Decimal("-1")]
    zero = _frame(local, ((0, 0, 1, 1), (0, 1, -1, 1)))
    assert (
        zero.select({"s": zero["input.x"].share(along=zero.axes)})
        .compute()
        .to_pandas()["s"]
        .isna()
        .all()
    )


def test_ordered_inputs_capture_names_subjects_and_independent_origins(
    local: LocalFrameServices,
) -> None:
    frame = _frame(local)
    x, y = frame["input.x"], frame["input.y"]
    named = frame.correlate(y, x, method="pearson")
    for current in (named, named.compute()):
        context = current.schema.value_contexts["coefficient"]
        assert isinstance(context, FramePairStatisticContext)
        assert context.x == FrameNamedValueInput("input.y", None)
        assert context.y == FrameNamedValueInput("input.x", A)
        assert current.schema.value_contexts["n_pairs"].statistic == "pair_count"
    derived = frame.reduce({"r": (x * 1).corr(y + 0, method="spearman")})
    for current in (derived, derived.compute()):
        context = current.schema.value_contexts["r"]
        assert isinstance(context, FramePairStatisticContext)
        assert context.x == FrameDerivedValueInput((A, B))
        assert context.y == FrameDerivedValueInput((C,))
    shared = frame.select({"s": (x * 1).share(along=frame.axes)})
    assert shared.schema.value_contexts["s"].numerator == FrameDerivedValueInput((A, B))
    assert shared.compute().schema.value_contexts == shared.schema.value_contexts


def test_same_origins_do_not_identify_same_expression_and_nested_share_executes(
    local: LocalFrameServices,
) -> None:
    frame = _frame(local, ((0, 0, 1, 1), (0, 1, 2, 3), (0, 2, 3, 2)))
    x, y = frame["input.x"], frame["input.y"]
    pair = frame.correlate(x + y, x - y, method="pearson")
    context = pair.schema.value_contexts["coefficient"]
    assert context.x == context.y == FrameDerivedValueInput((A, B, C))
    assert pair.compute().to_pandas()["coefficient"].iloc[0] == pytest.approx(0)
    nested = frame.select({"r": frame.over(x.share(along=frame.axes).pair_count(y))})
    assert isinstance(nested.schema.value_contexts["r"], FramePairStatisticContext)
    assert nested.compute().to_pandas()["r"].tolist() == [3, 3, 3]
    assert frame.where(x.share(along=frame.axes) > Decimal("0.3")).compute().to_pandas()[
        "input.x"
    ].tolist() == [2, 3]
    assert frame.top(1, by=x.share(along=frame.axes)).compute().to_pandas()["input.x"].tolist() == [
        3
    ]


def test_direct_copy_rename_fix_and_reduction_keep_only_valid_facts(
    local: LocalFrameServices,
) -> None:
    frame = _frame(local, ((0, 0, 1, 2), (0, 1, 3, 6), (1, 0, 2, 4)))
    output = frame.select(
        {
            "s": frame["input.x"].share(along=(frame.axes[1],)),
            "r": frame.over(
                frame["input.x"].corr(frame["input.y"], method="pearson"), by=(frame.axes[0],)
            ),
        }
    )
    copied = output.select(
        {"renamed": output["s"], "copy": output["s"], "r": output["r"], "new": output["s"] + 0}
    )
    assert tuple(copied.schema.value_contexts) == ("renamed", "copy", "r")
    assert copied.schema.value_contexts["renamed"].numerator.name == "input.x"
    for axis, expected in ((0, "complete"), (1, "restricted")):
        fixed = output.fix(output.axes[axis], 0)
        share = fixed.schema.value_contexts["s"]
        pair = fixed.schema.value_contexts["r"]
        assert share.row_scope == pair.row_scope == expected
        assert isinstance((share.groups if axis == 0 else share.along)[0], FrameFixedGroup)
        assert fixed.compute().schema.value_contexts == fixed.schema.value_contexts
    reduced = output.reduce({"sum": output["s"].sum()})
    assert reduced.schema.value_contexts["sum"] == FrameStatisticContext("sum", ())


def test_align_all_values_nested_presence_rename_prune_and_fix(local: LocalFrameServices) -> None:
    frame = _frame(local)
    literal = frame.select({"calculated": frame["input.x"] + 0})
    aligned = literal.align(literal)
    relation = aligned.schema.alignments[0]
    assert relation.input_id == aligned.schema.input_id
    assert relation.alignment_id == "alignment"
    assert relation.left.values == ("left.calculated",)
    assert relation.right.values == ("right.calculated",)
    assert relation.left.presence == ("left_present",)
    assert relation.left.coordinates == (
        FrameAlignmentAxis("group", "self"),
        FrameAlignmentAxis("sample", "self"),
    )
    selected = aligned.select(
        {
            "copy": aligned["left.calculated"],
            "renamed": aligned["left.calculated"],
            "derived": aligned["left.calculated"] + aligned["right.calculated"],
        }
    )
    assert selected.schema.alignments[0].left.values == ("copy", "renamed")
    assert selected.schema.alignments[0].right.values == ()
    assert (
        aligned.select(
            {"derived": aligned["left.calculated"] + aligned["right.calculated"]}
        ).schema.alignments
        == ()
    )
    nested = aligned.align(literal)
    assert tuple(a.alignment_id for a in nested.schema.alignments) == (
        "left.alignment",
        "alignment",
    )
    inner = nested.schema.alignments[0]
    assert inner.left.presence == ("left_present", "left.left_present")
    assert inner.right.presence == ("left_present", "left.right_present")
    fixed = nested.fix(nested.axes[0], 0)
    assert isinstance(fixed.schema.alignments[0].left.coordinates[0], FrameFixedGroup)
    assert fixed.compute().schema.alignments[0].left == fixed.schema.alignments[0].left
    assert nested.reduce({"n": mv.row_count()}).schema.alignments == ()


def test_outer_align_and_compare_preserve_original_complete_endpoint_scope(
    local: LocalFrameServices,
) -> None:
    frame = _frame(local)
    share = frame.select({"s": frame["input.x"].share(along=frame.axes)})
    extra = _frame(local, ((0, 2, 4, 8),))
    other = extra.select({"s": extra["input.x"]})
    aligned = share.align(other).compute()
    assert aligned.schema.value_contexts["left.s"].row_scope == "complete"
    rows = aligned.to_pandas()
    assert rows["left_present"].tolist() == [True, True, False]
    assert rows["left.s"].iloc[:2].tolist() == [Decimal("0.25"), Decimal("0.75")]
    assert pd.isna(rows["left.s"].iloc[2])
    same = share.align(share)
    assert all(c.row_scope == "complete" for c in same.schema.value_contexts.values())
    compared = share.compare(share)
    assert compared.schema.value_contexts["s"].row_scope == "complete"
    assert compared.schema.comparisons[0].groups[0].baseline_context.row_scope == "complete"
    restricted = share.top(0, by=share["s"])
    assert restricted.align(share).schema.value_contexts["left.s"].row_scope == "restricted"
    pair = frame.select(
        {"r": frame.over(frame["input.x"].corr(frame["input.y"], method="pearson"))}
    )
    assert all(c.row_scope == "complete" for c in pair.align(pair).schema.value_contexts.values())
    assert (
        pair.compare(pair).schema.comparisons[0].groups[0].current_context.row_scope == "complete"
    )


def test_time_mapping_retains_representable_nested_relation_and_shift_drops_only_baseline(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    time = case.session.time_scope(start="2026-08-15", end="2026-08-17").by(mv.grain("day"))
    source = case.session.observe({"q": case.catalog.get("metric:sales.revenue")}, by=(time,))
    equality = source.align(source)
    paired = equality.align(source, match=mv.ordinal())
    old, current = paired.schema.alignments
    assert old.alignment_id == "left.alignment"
    token = paired.schema.axes[0].token
    assert old.left.coordinates == old.right.coordinates == (FrameAlignmentAxis(token, "left"),)
    assert old.left.presence == ("left_present", "left.left_present")
    assert current.right.coordinates == (FrameAlignmentAxis(token, "right"),)
    assert paired.compute().schema.alignments[0].left == old.left
    equal_compare = equality.compare(equality)
    assert tuple(a.alignment_id for a in equal_compare.schema.alignments) == (
        "current.alignment",
        "reference.alignment",
    )
    same_match = equality.compare(equality, match=mv.ordinal())
    assert tuple(a.alignment_id for a in same_match.schema.alignments) == (
        "current.alignment",
        "reference.alignment",
    )
    earlier_time = case.session.time_scope(start="2026-08-14", end="2026-08-16").by(mv.grain("day"))
    earlier_source = case.session.observe(
        {"q": case.catalog.get("metric:sales.revenue")}, by=(earlier_time,)
    )
    earlier = earlier_source.align(earlier_source)
    shifted = equality.compare(earlier, match=mv.offset(periods=-1))
    assert tuple(a.alignment_id for a in shifted.schema.alignments) == ("current.alignment",)
    assert shifted.compute().schema.alignments[0].left.presence == (
        "current.present",
        "current.presence.left_present",
    )


def test_pending_source_inputs_survive_physical_type_closing_and_attribute_stays_restricted(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    region = catalog_value(case.catalog, "sales.customer.region", kind="field")
    frame = case.session.observe(
        {
            "a": case.catalog.get("metric:sales.revenue"),
            "b": case.catalog.get("metric:sales.line_revenue"),
            "c": case.catalog.get("metric:sales.order_count"),
        },
        by=(region,),
    )
    x, y = frame["a"] + frame["b"], frame["c"] * 1
    pair = frame.correlate(x, y, method="pearson")
    expected_x = tuple(
        o for o in frame.lineage.values["a"].value_origins + frame.lineage.values["b"].value_origins
    )
    expected_paths = {o.path for o in expected_x}
    for current in (pair, pair.compute()):
        context = current.schema.value_contexts["coefficient"]
        assert {o.path for o in context.x.origins} == expected_paths
        assert {o.path for o in context.y.origins} == {
            o.path for o in frame.lineage.values["c"].value_origins
        }
        assert "sales.revenue" in {o.path for o in context.x.origins}
        assert "sales.revenue" not in {o.path for o in context.y.origins}
    shares = frame.select({"s": x.share(along=frame.axes)})
    assert shares.compute().schema.value_contexts == shares.schema.value_contexts
    broadcast = frame.select({"r": frame.over(frame["a"].corr(frame["c"], method="pearson"))})
    for rejected in (shares, pair, broadcast):
        with pytest.raises(AnalysisError) as caught:
            rejected.attribute(by=(region,))
        assert caught.value.location == "frame.attribute"
        assert caught.value.repair is not None


def test_invalid_current_alignment_references_are_rejected(local: LocalFrameServices) -> None:
    frame = _frame(local)
    aligned = frame.align(frame)
    relationship = aligned._schema.alignments[0]
    for side in (
        replace(relationship.left, values=("absent",)),
        replace(relationship.left, presence=("absent",)),
        replace(relationship.left, coordinates=(FrameAlignmentAxis("group", "left"),)),
    ):
        with pytest.raises(AnalysisError):
            validate_current_facts(
                replace(aligned._schema, alignments=(replace(relationship, left=side),))
            )


@pytest.mark.parametrize("damage", ("share_group", "left_coordinates", "right_axis"))
def test_canonical_wire_rejects_incomplete_active_axis_directories(
    local: LocalFrameServices,
    damage: str,
) -> None:
    import json

    from marivo.analysis.core.artifact_data import ArtifactManifest
    from marivo.analysis.materialization import terminal_protocol
    from marivo.analysis.materialization.contracts import canonical_json
    from marivo.analysis.materialization.errors import IntegrityError
    from marivo.analysis.materialization.graph_reading import open_manifest

    frame = _frame(local)
    shares = frame.select({"share": frame["input.x"].share(along=(frame.axes[1],))})
    saved = shares.align(shares).persist()
    manifest = open_manifest(local.store, local.session_ref, saved.artifact_id)
    wire = json.loads(terminal_protocol.encode(manifest))
    if damage == "share_group":
        share = next(c for c in wire["contexts"] if c["record"] == "FrameShareContext")
        share["fields"]["groups"] = []
    else:
        relationship = wire["value"]["fields"]["data"]["fields"]["schema"]["fields"]["alignments"][
            0
        ]
        side = relationship["fields"]["left" if damage == "left_coordinates" else "right"]["fields"]
        side["coordinates"] = [] if damage == "left_coordinates" else side["coordinates"][:1]
    with pytest.raises(IntegrityError):
        terminal_protocol.decode(canonical_json(wire), ArtifactManifest)


def test_fixed_axes_and_later_normalization_keep_only_their_captured_ownership(
    local: LocalFrameServices,
) -> None:
    frame = _frame(local)
    before = frame.select({"share": frame["input.x"].share(along=(frame.axes[1],))})
    fixed_existing = before.fix(before.axes[0], 0)
    assert isinstance(fixed_existing.schema.value_contexts["share"].groups[0], FrameFixedGroup)
    assert fixed_existing.align(fixed_existing).compute().schema.alignments
    fixed_first = frame.fix(frame.axes[0], 0)
    new = fixed_first.select({"share": fixed_first["input.x"].share(along=fixed_first.axes)})
    context = new.schema.value_contexts["share"]
    assert context.groups == ()
    assert context.along == (FrameAxisGroup("sample"),)
    assert new.compute().schema.fixed_axes
    assert new.align(new).compute().schema.value_contexts["left.share"] == context


def test_nested_target_context_keeps_parent_axes_without_claiming_fine_axes(
    local: LocalFrameServices,
) -> None:
    from marivo.analysis.core.frame_interpretation import (
        FrameDecomposition,
        FrameDecompositionField,
        FrameLevelTarget,
    )

    frame = _frame(local)
    share = frame.select({"share": frame["input.x"].share(along=(frame.axes[1],))})
    context = share.schema.value_contexts["share"]
    nested = DataSchema(
        (
            *frame._schema.axes,
            DataAxis("detail", (DataColumn("d", "detail", INT64, False),), name="detail"),
        ),
        (DataColumn("effect", "effect", INT64, True),),
        value_paths=(("effect",),),
        decompositions=(
            FrameDecomposition(
                "closed-carrier",
                FrameLevelTarget("captured.share", context),
                (FrameAxisGroup("group"), FrameAxisGroup("sample")),
                "complete",
                (FrameDecompositionField("value", "contribution", "effect"),),
            ),
        ),
    )
    validate_current_facts(nested)
    assert nested.decompositions[0].target.context == context


def test_schema_and_frame_cards_disclose_bounded_alignment_directory_without_rows(
    local: LocalFrameServices,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization.graph_reading import FrameReader, OutputRead

    frame = _frame(local)
    leaf = frame.select({"v": frame["input.x"]})
    first = leaf.align(leaf)
    second = first.align(first)
    nested = second.align(leaf)
    saved = nested.persist()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("metadata cards must not read rows, coordinates or method payloads")

    monkeypatch.setattr(FrameReader, "data", forbidden)
    monkeypatch.setattr(FrameReader, "method_facts", forbidden)
    monkeypatch.setattr(OutputRead, "_open", forbidden)
    for current in (nested, saved):
        current.schema.show()
        schema_card = capsys.readouterr().out
        assert "alignments.count: 4" in schema_card
        assert "alignments[0]" in schema_card
        assert "alignments[2]" in schema_card
        assert "alignments[3]" not in schema_card
        assert "alignments.more: 1 omitted; read .alignments" in schema_card
        assert ".alignments" in schema_card
        assert len(schema_card.encode()) <= 8192
        current.show(n=0)
        frame_card = capsys.readouterr().out
        assert "alignments.count: 4" in frame_card
        assert "alignments[0]" in frame_card
        assert "alignments[2]" in frame_card
        assert "alignments[3]" not in frame_card
        assert "alignments.more: 1 omitted" in frame_card
        assert ".schema.alignments" in frame_card
        assert len(frame_card.encode()) <= 8192
