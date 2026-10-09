"""Independent reference arithmetic and public source/fixed acceptance."""

from decimal import Decimal
from fractions import Fraction
from typing import Literal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.model import Defined, Null, Undefined, Unknown
from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType
from marivo.analysis.methods.references import standardize, weight_sum
from tests.shared_fixtures import DslCaseFactory, analysis_dsl_rows, export_dsl_parquet_models
from tests.support.paths import PROJECT_ROOT


def test_exact_standardization_and_zero_weight_cells() -> None:
    i, f = ScalarType("int64"), ScalarType("float64")
    assert standardize((Defined(2**53 + 1), Defined(3)), (0.5, 0.5), i, f) == Defined(
        float(Fraction(2**53 + 4, 2))
    )
    for cell in (Null("empty_contribution"), Undefined("zero_denominator"), Unknown("coverage")):
        assert standardize((Defined(7), cell), (1, 0), i, i) == Defined(7.0)
        with pytest.raises(ValueError, match="positive-weight"):
            standardize((Defined(7), cell), (0.5, 0.5), i, f)
    d = DecimalType(18, 6)
    assert standardize(
        (Defined(Decimal("1.000001")), Defined(Decimal("2.000000"))),
        (Decimal("0.500000"), Decimal("0.500000")),
        d,
        d,
    ) == Defined(Decimal("1.500000"))
    with pytest.raises(ValueError, match="Duration"):
        standardize((Defined(1),), (1,), DurationType("us"), i)


@pytest.mark.parametrize("damage", [None, "value", "duplicate"])
def test_standardization_proof_uses_keys_and_preserves_damage(
    damage: Literal["value", "duplicate"] | None,
) -> None:
    import pyarrow as pa

    from marivo.analysis.core.model import Binding, Coordinate, DomainSignature
    from marivo.analysis.core.rules import ReferenceDerive
    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.graph_exchange import ExchangePart
    from marivo.analysis.materialization.graph_reference import finish

    schema = pa.schema(
        [
            pa.field("key_0", pa.string()),
            pa.field("value", pa.int64()),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
            pa.field("error_bound", pa.float64()),
        ]
    )
    values = pa.Table.from_pylist(
        [
            {
                "key_0": "a",
                "value": 2,
                "cell_tag": "defined",
                "cell_reason": None,
                "error_bound": 0.0,
            },
            {
                "key_0": "b",
                "value": 4,
                "cell_tag": "defined",
                "cell_reason": None,
                "error_bound": 0.0,
            },
        ],
        schema=schema,
    )
    proof = values.take([1, 0])
    if damage == "value":
        proof = proof.set_column(1, schema.field("value"), pa.array([5, 2], type=pa.int64()))
    elif damage == "duplicate":
        proof = proof.take([0, 1, 1])
    weights = values.set_column(1, pa.field("value", pa.float64()), pa.array([0.5, 0.5]))
    domain = DomainSignature(Binding("proof", "sales", "facts", "all"), "singleton", (), (), "all")
    params = ReferenceDerive(
        "standardize",
        domain,
        "weights",
        "facts",
        "all",
        (Coordinate(ms.ref.entity("sales.facts"), "tenant", "identity"),),
        ms.ref.entity("sales.facts"),
    )
    parts = (
        ExchangePart("stratum_values", values),
        ExchangePart("reference_proof", proof),
        ExchangePart("fixed_reference", weights),
        ExchangePart("strata", weights.select(["key_0"]).take([1, 0])),
    )
    if damage is None:
        assert finish(params, ScalarType("float64"), parts)["value"].to_pylist() == [3.0]
    else:
        with pytest.raises(AnalysisError, match="proof differs"):
            finish(params, ScalarType("float64"), parts)


@pytest.mark.parametrize("weights", [(0.5, 0.5), (0.1, 0.2, 0.7), (1.0 + 5e-13, 0.0)])
def test_order_independent_represented_weight_sum(weights: tuple[float, ...]) -> None:
    expected = sum((Fraction(value) for value in weights), Fraction())
    assert weight_sum(weights, ScalarType("float64")) == expected
    assert weight_sum(tuple(reversed(weights)), ScalarType("float64")) == expected


@pytest.mark.parametrize(
    "weights", [(), (0.0,), (-0.1, 1.1), (float("inf"),), (float("nan"),), (1.0 + 2e-12,)]
)
def test_invalid_weights_reject(weights: tuple[float, ...]) -> None:
    with pytest.raises((ValueError, OverflowError)):
        weight_sum(weights, ScalarType("float64"))


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_public_references_source_and_fixed(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    region = members.read(ms.ref.dimension("sales.customer.region"))
    values = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"sales.{case.names.buyer}"),
        by=(mv.member(),),
    )
    facts = analysis_dsl_rows("j2")
    counts = {
        member: sum(
            buyer == member and str(date).startswith("2026-08")
            for _, buyer, _, _, date, _ in facts.orders
        )
        for member, *_ in facts.customers
    }
    total_count = sum(counts.values())
    for current in (values, values.execute()):
        total = current.rollup()
        if isinstance(current, mv.MaterializedNumericRelation):
            total = total.execute()
        shares = current.share_of(total).execute()
        frame = shares.to_pandas()
        assert dict(zip(frame.member, frame.value, strict=True)) == {
            key: float(Fraction(value, total_count)) for key, value in counts.items()
        }
        selected = shares.where(shares.value.gt(1)).execute()
        assert selected._dataset.verified().parts[0].table.num_rows == 1
        assert "relation.rollup()" not in {action.call for action in selected.contract().actions}
        assert (
            dict(selected.contract()._facts)["reference"]
            == dict(shares.contract()._facts)["reference"]
        )
        shares.show()
        assert "current_partition: complete" in capsys.readouterr().out
        selected.show()
        selected_card = capsys.readouterr().out
        assert "current_partition: partial" in selected_card
        assert f"reference_denominator: {total_count}" in selected_card
        assert "nonnegative_range: proved [0,1]" in selected_card
    selected_members = values.where(values.value.gt(0)).members()
    result = selected_members.penetration_in(members).execute()
    assert result.to_pandas().value.tolist() == [
        float(Fraction(sum(value > 0 for value in counts.values()), len(counts)))
    ]
    groups = values.group_by(region).rollup()
    weights = groups.share_of(groups.rollup())
    reference = mv.reference_weights(weights, strata=(region,), unit=ms.ref.entity("sales.order"))
    standardized = groups.standardize(reference=reference).execute()
    grouped = groups.execute().to_pandas()
    expected = sum((Fraction(int(value) ** 2, total_count) for value in grouped.value), Fraction())
    assert standardized.to_pandas().value.tolist() == [float(expected)]
    assert "ReferenceWeights" in repr(reference)
    standardized.show()
    card = capsys.readouterr().out
    assert "represented_weight_sum_deviation: 0" in card
    assert "arithmetic_error_bound:" in card
    assert "weighted stratum value; no actual population claim" in card
    selected = standardized.where(standardized.value.gt(0)).execute()
    assert dict(selected.contract()._facts)["statistical_entity"] == "sales.order"


@pytest.mark.runtime
@pytest.mark.parametrize(
    "physical,parquet",
    [
        (physical, parquet)
        for physical in ("BIGINT", "DOUBLE", "DECIMAL(30,6)", "duration")
        for parquet in (False, True)
    ]
    + [("duration_" + unit, True) for unit in ("s", "ms", "ns")],
)
def test_public_share_numeric_matrix(
    analysis_dsl_case_factory: DslCaseFactory, physical: str, parquet: bool
) -> None:
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        if physical.startswith("duration") and not parquet:
            db.execute(
                'ALTER TABLE "order" ALTER amount TYPE INTERVAL USING to_microseconds(amount)'
            )
        elif not physical.startswith("duration"):
            db.execute(f'ALTER TABLE "order" ALTER amount TYPE {physical}')
    if parquet:
        export_dsl_parquet_models(case, case.root)
        if physical.startswith("duration"):
            path = case.root / "source_files/order.parquet"
            table = pq.read_table(path)
            pq.write_table(
                table.set_column(
                    table.schema.get_field_index("amount"),
                    "amount",
                    table["amount"].cast(pa.duration(physical.partition("_")[2] or "us")),
                ),
                path,
            )
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    values = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    if physical.startswith("duration"):
        category = members.read(ms.ref.dimension("sales.customer.region"))
        grouped = values.group_by(category).rollup()
        assert not any("standardize(" in action.call for action in grouped.contract().actions)
        count_groups = (
            members.observe(
                ms.ref.metric("sales.order_count"),
                during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
                via=ms.ref.relationship("sales.order_buyer"),
                by=(mv.member(),),
            )
            .group_by(category)
            .rollup()
        )
        with pytest.raises(AnalysisError):
            grouped.standardize(
                reference=mv.reference_weights(
                    count_groups.share_of(count_groups.rollup()),
                    strata=(category,),
                    unit=ms.ref.entity("sales.order"),
                )
            )
    for current in (values, values.execute()):
        reference = current.rollup()
        if isinstance(current, mv.MaterializedNumericRelation):
            reference = reference.execute()
        shares = current.share_of(reference).execute().to_pandas().set_index("member")
        raw = analysis_dsl_rows("j2")
        totals = {
            member: sum(
                amount
                for _, buyer, _, _, date, amount in raw.orders
                if buyer == member and str(date).startswith("2026-08")
            )
            for member, _ in raw.customers
        }
        expected = {
            member: Fraction(total, sum(totals.values())) for member, total in totals.items()
        }
        if physical.startswith("DECIMAL"):
            expected_decimal = {
                key: (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
                    Decimal("0.000001")
                )
                for key, value in expected.items()
            }
            assert shares.value.to_dict() == expected_decimal
        else:
            assert shares.value.to_dict() == {key: float(value) for key, value in expected.items()}


@pytest.mark.runtime
@pytest.mark.parametrize("physical", ["BIGINT", "DOUBLE", "DECIMAL(30,6)"])
@pytest.mark.parametrize("parquet", [False, True])
def test_original_metric_standardization_matrix(
    analysis_dsl_case_factory: DslCaseFactory,
    physical: str,
    parquet: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    import duckdb

    from marivo.analysis.public_dsl import MetricInputValue
    from tests.analysis.materialization.domain_recovery_worker import snapshot
    from tests.analysis.statistics.standardization_recovery_worker import parts
    from tests.support.json import Json, checked, encode, read
    from tests.support.source_trace import capture_source

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(f'ALTER TABLE "order" ALTER amount TYPE {physical}')
        db.execute(f'ALTER TABLE "order" ADD COLUMN weight {physical} DEFAULT 1')
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace("granularity='second',", "granularity='second', is_default=True,")
        + "\nweight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n"
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    trace = capture_source(monkeypatch)
    members = case.session.members(ms.ref.entity("sales.customer"))
    categories = members.read(ms.ref.dimension("sales.customer.region"))
    assert isinstance(categories, mv.LogicalCategoryRelation)
    amount = ms.ref.measure("sales.order.amount")
    base = ms.ref.metric("sales.revenue")
    metrics: dict[str, MetricInputValue] = {
        "sum": base,
        "mean": mv.runtime_metric.aggregate(amount, agg="mean", label="mean"),
        "weighted_mean": mv.runtime_metric.weighted_mean(
            amount, ms.ref.measure("sales.order.weight"), label="weighted"
        ),
        "ratio": mv.runtime_metric.ratio(base, base, label="ratio"),
        "linear": mv.runtime_metric.linear(add=[base, base], label="linear"),
    }
    if physical == "BIGINT":
        metrics["count"] = ms.ref.metric("sales.order_count")
    scope = mv.time_scope(start="2026-08-01", end="2026-09-01")
    volume = (
        members.observe(
            base,
            during=scope,
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        )
        .group_by(categories)
        .rollup()
    )
    weights = volume.share_of(volume.rollup())
    offline = physical != "BIGINT"
    originals: dict[str, Json] = {}
    original_parts: dict[str, Json] = {}
    sources: dict[str, Json] = {}
    source_parts: dict[str, Json] = {}
    expectations: dict[str, Json] = {}
    retained_weights, retained_categories = weights.execute(), categories.execute()
    assert isinstance(retained_categories, mv.MaterializedCategoryRelation)
    for name, value in (("weights", retained_weights), ("categories", retained_categories)):
        originals[name], original_parts[name] = snapshot(value), parts(value)
    for kind, metric in metrics.items():
        groups = (
            members.observe(
                metric,
                during=scope,
                via=ms.ref.relationship("sales.order_buyer"),
                by=(mv.member(),),
            )
            .group_by(categories)
            .rollup()
        )
        reference = mv.reference_weights(
            weights, strata=(categories,), unit=ms.ref.entity("sales.order")
        )
        source = groups.standardize(reference=reference).execute()
        fixed_groups = groups.execute()
        fixed_weights, fixed_categories = retained_weights, retained_categories
        fixed_reference = mv.reference_weights(
            fixed_weights, strata=(fixed_categories,), unit=ms.ref.entity("sales.order")
        )
        fixed = None if offline else fixed_groups.standardize(reference=fixed_reference).execute()
        raw = analysis_dsl_rows("j2")
        region_by_member = dict(raw.customers)
        totals_by_region = dict.fromkeys(region_by_member.values(), Fraction())
        counts_by_region = dict.fromkeys(region_by_member.values(), 0)
        for _, buyer, _, _, date, amount_value in raw.orders:
            if str(date).startswith("2026-08"):
                region = region_by_member[buyer]
                totals_by_region[region] += Fraction(amount_value)
                counts_by_region[region] += 1
        exact_expected = Fraction()
        for region, total in totals_by_region.items():
            weight = total / sum(totals_by_region.values())
            if weight == 0:
                continue
            stratum = {
                "sum": total,
                "count": Fraction(counts_by_region[region]),
                "mean": total / counts_by_region[region],
                "weighted_mean": total / counts_by_region[region],
                "ratio": Fraction(1),
                "linear": 2 * total,
            }[kind]
            exact_expected += stratum * weight
        expected = (
            (Decimal(exact_expected.numerator) / Decimal(exact_expected.denominator)).quantize(
                Decimal("0.000001")
            )
            if physical.startswith("DECIMAL")
            else float(exact_expected)
        )
        assert source.to_pandas().value.tolist() == [expected]
        sources[kind] = snapshot(source)
        source_parts[kind] = parts(source)
        originals["groups:" + kind] = snapshot(fixed_groups)
        original_parts["groups:" + kind] = parts(fixed_groups)
        expectations[kind] = str(expected) if isinstance(expected, Decimal) else expected
        if fixed is not None:
            assert fixed.to_pandas().value.tolist() == [expected]
            assert fixed._dataset is not None
            assert fixed._dataset.verified().contract.state_kind == "standardized"
            assert not any(
                action.call == "relation.rollup()" for action in fixed.contract().actions
            )
        if kind == "linear":
            raw = analysis_dsl_rows("j2")
            region_by_member = dict(raw.customers)
            totals = dict.fromkeys(region_by_member.values(), Fraction())
            for _, buyer, _, _, date, order_amount in raw.orders:
                if str(date).startswith("2026-08"):
                    totals[region_by_member[buyer]] += 2 * Fraction(order_amount)
            expected_shares = {
                key: Fraction(total, sum(totals.values())) for key, total in totals.items()
            }
            for current in (groups, groups.execute()):
                shares = current.share_of(current.rollup()).execute().to_pandas()
                assert {
                    key: Fraction(value)
                    for key, value in zip(shares.group, shares.value, strict=True)
                } == expected_shares

    if offline:
        state: dict[str, Json] = {
            "phase": "produce",
            "pid": os.getpid(),
            "session": case.session.id,
            "physical": physical,
            "form": "parquet" if parquet else "table",
            "originals": originals,
            "parts": original_parts,
            "source_results": sources,
            "source_parts": source_parts,
            "expected": expectations,
            "native_sql": [*trace.native_sql],
            "source_closed": all(owner._closed for owner in trace.owners),
        }
        assert trace.native_sql and trace.owners and state["source_closed"] is True
        (case.root / "r94-standardization.json").write_bytes(encode(state))
        shutil.rmtree(case.root / "models")
        case.database_path.unlink()
        if (case.root / "source_files").exists():
            shutil.rmtree(case.root / "source_files")
        reports: list[Json] = [state]
        for phase in ("fixed", "cold"):
            output = case.root / ("standardization-" + phase + ".json")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tests.analysis.statistics.standardization_recovery_worker",
                    str(case.root),
                    phase,
                    str(output),
                ],
                cwd=PROJECT_ROOT,
                env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert completed.returncode == 0, completed.stdout + completed.stderr
            reports.append(read(output))
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            name = (
                "standardization-"
                + ("decimal" if physical.startswith("DECIMAL") else "float")
                + ("-parquet" if parquet else "-table")
                + ".json"
            )
            Path(directory, name).write_bytes(encode({"reports": checked(reports)}))


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_public_exact_integer_reference_weights(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    import duckdb
    import pyarrow as pa

    case = analysis_dsl_case_factory("j2")
    raw = analysis_dsl_rows("j2")
    kept = next(row for row in raw.orders if str(row[4]).startswith("2026-08"))
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order" WHERE order_id != ?', [kept[0]])
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace("granularity='second',", "granularity='second', is_default=True,")
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    category = members.read(ms.ref.dimension("sales.customer.region"))
    groups = (
        members.observe(
            ms.ref.metric("sales.order_count"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        )
        .group_by(category)
        .rollup()
    )
    means = (
        members.observe(
            mv.runtime_metric.aggregate(
                ms.ref.measure("sales.order.amount"), agg="mean", label="sample"
            ),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        )
        .group_by(category)
        .rollup()
    )
    for current, classification, receiver in (
        (groups, category, means),
        (groups.execute(), category.execute(), means.execute()),
    ):
        result = current.standardize(
            reference=mv.reference_weights(
                current, strata=(classification,), unit=ms.ref.entity("sales.order")
            )
        ).execute()
        assert result.to_pandas().value.tolist() == [float(Fraction(1))]
        assert result._dataset.verified().parts[0].table.schema.field("value").type == pa.int64()
        standardized = receiver.standardize(
            reference=mv.reference_weights(
                current, strata=(classification,), unit=ms.ref.entity("sales.order")
            )
        ).execute()
        assert standardized.to_pandas().value.tolist() == [float(Fraction(kept[5]))]
        retained = standardized._dataset.verified().parts
        stratum_values = next(part.table for part in retained if part.role == "stratum_values")
        assert stratum_values["cell_tag"].to_pylist().count("null") == 2


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_reference_cold_continuations_and_corruption(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    import os
    import subprocess
    import sys
    from dataclasses import replace

    import pyarrow as pa

    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.errors import IntegrityError, MaterializationError
    from marivo.analysis.materialization.graph_exchange import ExchangePart, from_arrow

    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    category = members.read(ms.ref.dimension("sales.customer.region"))
    values = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    groups = values.group_by(category).rollup()
    weights = groups.share_of(groups.rollup())
    saved = (
        values.share_of(values.rollup()).execute(),
        members.penetration_in(members).execute(),
        groups.standardize(
            reference=mv.reference_weights(
                weights, strata=(category,), unit=ms.ref.entity("sales.order")
            )
        ).execute(),
    )
    for relation in saved:
        checked = relation._dataset.verified()
        descriptor = relation._dataset.artifact.descriptor
        assert descriptor.method_state.contract_version == 1
        with pytest.raises(IntegrityError, match="Re-execute the reference"):
            replace(descriptor.method_state, contract_version=2)
        assert checked.method_state is not None
        corrupted_state = checked.method_state.set_column(
            checked.method_state.schema.get_field_index("error_bound"),
            "error_bound",
            pa.array([99.0] * checked.method_state.num_rows),
        )
        with pytest.raises(MaterializationError):
            from_arrow(
                checked.primary,
                checked.contract,
                parts=checked.parts,
                method_state=corrupted_state,
                completed_checks=checked.completed_checks,
            )
        for part in checked.parts:
            with pytest.raises(MaterializationError):
                from_arrow(
                    checked.primary,
                    checked.contract,
                    parts=tuple(item for item in checked.parts if item is not part),
                    method_state=checked.method_state,
                    completed_checks=checked.completed_checks,
                )
            if part.table.num_rows:
                column = (
                    "value" if "value" in part.table.column_names else part.table.column_names[0]
                )
                dtype = part.table.schema.field(column).type
                replacement = (
                    pa.array([99] * part.table.num_rows, type=dtype)
                    if pa.types.is_integer(dtype) or pa.types.is_floating(dtype)
                    else pa.array(["absent"] * part.table.num_rows, type=dtype)
                )
                corrupted = part.table.set_column(
                    part.table.schema.get_field_index(column), column, replacement
                )
                with pytest.raises((AnalysisError, ValueError)):
                    from_arrow(
                        checked.primary,
                        checked.contract,
                        parts=tuple(
                            ExchangePart(item.role, corrupted) if item is part else item
                            for item in checked.parts
                        ),
                        method_state=checked.method_state,
                        completed_checks=checked.completed_checks,
                    )
        for receipt in descriptor.parts:
            local = receipt.local
            part_path = (
                case.root / local.project_relative_path / local.file_manifest[0].relative_path
            )
            contents = part_path.read_bytes()
            try:
                part_path.unlink()
                with pytest.raises(IntegrityError):
                    relation._dataset.verified()
            finally:
                part_path.write_bytes(contents)
    source_path = case.root / "source_files" if parquet else case.database_path
    offline = source_path.with_name(source_path.name + ".offline")
    source_path.rename(offline)
    script = """
import sys
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
def unavailable(*args, **kwargs):
    raise AssertionError('fixed reference recovery cannot open Semantic or DuckDB')
duckdb.connect = unavailable
sys.modules['duckdb'] = None
ms.load = unavailable
session = mv.session.resume(sys.argv[1], by='id')
for ref in sys.argv[2:]:
    saved = session.artifact(ref)
    original = saved._dataset.verified()
    selected = saved.where(saved.value.gt(-1)).execute()
    assert selected.to_pandas().value.tolist() == saved.to_pandas().value.tolist()
    retained = selected._dataset.verified()
    assert all(a.table.equals(b.table) for a,b in zip(original.parts,retained.parts,strict=True))
    assert session.artifact(selected.state.artifact_ref.ref).to_pandas().equals(selected.to_pandas())
"""
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                case.session.id,
                *(relation.state.artifact_ref.ref for relation in saved),
            ],
            cwd=case.root,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        offline.rename(source_path)
    assert completed.returncode == 0, completed.stderr
    # Receipt verification is mandatory before recovery or fixed cache reuse.
    receipt = saved[0]._dataset.artifact.descriptor.parts[0].local
    path = case.root / receipt.project_relative_path / receipt.file_manifest[0].relative_path
    original_bytes = path.read_bytes()
    continuation = saved[0].where(saved[0].value.gt(0))
    continuation.execute()
    try:
        path.write_bytes(b"invalid retained reference")
        with pytest.raises(IntegrityError):
            continuation.execute()
    finally:
        path.write_bytes(original_bytes)


@pytest.mark.runtime
def test_static_reference_rejections_are_zero_read(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import FrozenInstanceError

    from marivo.analysis.errors import AnalysisError
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    category = members.read(ms.ref.dimension("sales.customer.region"))
    values = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    fixed = values.execute()
    other = analysis_dsl_case_factory("j2")
    foreign = other.session.members(ms.ref.entity("sales.customer"))
    run = values._runtime.last_run_ref

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("reference construction cannot read business rows")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    groups = values.group_by(category).rollup()
    weights = groups.share_of(groups.rollup())
    reference = mv.reference_weights(weights, strata=(category,), unit=ms.ref.entity("sales.order"))
    groups.standardize(reference=reference)
    values.share_of(values.rollup())
    members.penetration_in(members)
    with pytest.raises(FrozenInstanceError):
        reference._unit = ms.ref.entity("sales.customer")
    assert not hasattr(reference, "execute")
    with pytest.raises(AnalysisError):
        values.share_of(fixed.rollup())
    with pytest.raises(AnalysisError):
        members.penetration_in(foreign)
    with pytest.raises(AnalysisError):
        values.share_of(values)
    with pytest.raises(AnalysisError):
        groups.standardize(
            reference=mv.reference_weights(
                weights, strata=(category,), unit=ms.ref.entity("sales.customer")
            )
        )
    with pytest.raises(AnalysisError):
        mv.reference_weights(
            weights, strata=(category, category), unit=ms.ref.entity("sales.order")
        )
    identity = members.read(ms.ref.dimension("sales.customer.customer_id"))
    with pytest.raises(AnalysisError):
        mv.reference_weights(weights, strata=(identity,), unit=ms.ref.entity("sales.order"))
    independent = members.read(ms.ref.dimension("sales.customer.region"))
    with pytest.raises(AnalysisError):
        mv.reference_weights(weights, strata=(independent,), unit=ms.ref.entity("sales.order"))
    multi = values.group_by(category, identity).rollup()
    with pytest.raises(AnalysisError):
        mv.reference_weights(
            multi.share_of(multi.rollup()),
            strata=(identity, category),
            unit=ms.ref.entity("sales.order"),
        )
    other_time = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    with pytest.raises(AnalysisError):
        other_time.group_by(category).rollup().standardize(reference=reference)
    ordinary = values.ratio(values, pairing=mv.ExactKeys())
    with pytest.raises(AnalysisError):
        ordinary.share_of(values.rollup())
    with pytest.raises(AnalysisError):
        ordinary.standardize(reference=reference)
    assert values._runtime.last_run_ref == run


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_composite_penetration_overlap_and_empty(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("ALTER TABLE customer ADD tenant BIGINT DEFAULT 7")
        db.execute("INSERT INTO customer VALUES ('A', 'north', 8)")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            "primary_key=['customer_id']", "primary_key=['tenant', 'customer_id']"
        )
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    category = members.read(ms.ref.dimension("sales.customer.region"))
    for current, classification in ((members, category), (members.execute(), category.execute())):
        east_or_south = classification.where(mv.not_(classification.value.eq("west"))).members()
        east_or_west = classification.where(mv.not_(classification.value.eq("south"))).members()
        first = east_or_south.penetration_in(current).execute().to_pandas().value.iloc[0]
        second = east_or_west.penetration_in(current).execute().to_pandas().value.iloc[0]
        assert first == second == float(Fraction(4, 5))
        assert first + second > 1
        empty = classification.where(classification.value.eq("absent")).members()
        result = empty.penetration_in(empty).execute().to_pandas()
        assert result.cell_tag.tolist() == ["undefined"]
        assert result.cell_reason.tolist() == ["empty_reference"]


@pytest.mark.runtime
@pytest.mark.parametrize("signed", [False, True])
def test_zero_and_signed_share_support(
    analysis_dsl_case_factory: DslCaseFactory, signed: bool
) -> None:
    import duckdb

    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('UPDATE "order" SET amount=? WHERE customer_id=?', [-30 if signed else 0, "C"])
        if not signed:
            db.execute('UPDATE "order" SET amount=0')
    members = case.session.members(ms.ref.entity("sales.customer"))
    values = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    reference = values.rollup()
    shares = values.share_of(reference).execute()
    frame = shares.to_pandas().set_index("member")
    if signed:
        assert frame.value.to_dict() == {"A": 0.4, "B": 0.8, "C": -0.2, "D": 0.0}
        limited = values.where(values.value.gt(0))
        result = limited.share_of(reference).execute()
        assert result.to_pandas().value.sum() == pytest.approx(1.2)
        assert result._dataset.verified().parts[0].table["value"].to_pylist() == [150]
        with pytest.raises(AnalysisError):
            values.share_of(limited.rollup())
    else:
        assert frame.cell_tag.eq("undefined").all()
        assert frame.cell_reason.eq("zero_denominator").all()
        assert frame.value.isna().all()


@pytest.mark.runtime
@pytest.mark.parametrize(
    "point", ["graph_part_written", "graph_files_published", "graph_receipts_verified"]
)
def test_reference_publication_faults_are_atomic(
    analysis_dsl_case_factory: DslCaseFactory, point: str
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    values = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    fixed = values.execute()
    before = {
        path
        for path in (case.root / ".marivo").rglob("*")
        if path.is_file() and path.suffix == ".parquet"
    }

    def fail(event: str) -> None:
        if event == point:
            raise RuntimeError("injected reference publication failure")

    values._runtime._hook = fail
    try:
        with pytest.raises(AnalysisError):
            values.share_of(values.rollup()).execute()
    finally:
        values._runtime._hook = None
    after = {
        path
        for path in (case.root / ".marivo").rglob("*")
        if path.is_file() and path.suffix == ".parquet"
    }
    assert after == before
    assert fixed.to_pandas().value.tolist() == [1, 1, 1, 1]
    assert values.share_of(values.rollup()).execute().to_pandas().value.tolist() == [0.25] * 4


@pytest.mark.parametrize("bad", [True, 2**63, float("inf"), float("nan"), "7"])
def test_zero_weight_does_not_hide_malformed_defined(bad: object) -> None:
    with pytest.raises((ValueError, OverflowError)):
        standardize((Defined(7), Defined(bad)), (1, 0), ScalarType("int64"), ScalarType("int64"))


def test_tiny_positive_weights_and_single_order_independent_finish() -> None:
    f = ScalarType("float64")
    with pytest.raises(ValueError, match="positive-weight"):
        standardize((Defined(7.0), Undefined("zero_denominator")), (1.0, 1e-300), f, f)
    values = (Defined(1e16), Defined(1.0), Defined(-1e16))
    weights = (1 / 3, 1 / 3, 1 / 3)
    expected = float(
        sum(
            (
                Fraction(cell.value) * Fraction(weight)
                for cell, weight in zip(values, weights, strict=True)
            ),
            Fraction(),
        )
    )
    assert standardize(values, weights, f, f).value == expected
    assert standardize(tuple(reversed(values)), tuple(reversed(weights)), f, f).value == expected
    d = DecimalType(18, 6)
    with pytest.raises(ValueError):
        weight_sum((Decimal("0.999999"),), d)
    for next_value in (1.0 + 1e-12, 1.0 - 1e-12):
        if abs(Fraction(next_value) - 1) <= Fraction(1e-12):
            assert weight_sum((next_value,), f) == Fraction(next_value)
        else:
            with pytest.raises(ValueError):
                weight_sum((next_value,), f)


@pytest.mark.runtime
def test_shared_reference_source_reexecution_and_fixed_inputs(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb

    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization import graph_reference

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    values = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    share = values.share_of(values.rollup())
    calls: list[str] = []
    finish = graph_reference.result

    def counted(node, parts, binding):
        calls.append(node.identity)
        return finish(node, parts, binding)

    monkeypatch.setattr(graph_reference, "result", counted)
    assert (
        share.ratio(share, pairing=mv.ExactKeys()).execute().to_pandas().value.tolist() == [1.0] * 4
    )
    assert calls == [share._node.root.identity]
    fixed = values.execute()
    reference = fixed.rollup().execute()
    captured = fixed.share_of(reference)
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            ["extra", "A", "web", "paid", "2026-08-15", 1],
        )
    current = share.execute().to_pandas().set_index("member")
    assert current.value.to_dict() == {"A": 0.4, "B": 0.2, "C": 0.2, "D": 0.2}
    assert captured.execute().to_pandas().value.tolist() == [0.25] * 4
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        with pytest.raises(AnalysisError):
            share.execute()
        assert captured.execute().to_pandas().value.tolist() == [0.25] * 4
    finally:
        offline.rename(case.database_path)


@pytest.mark.runtime
@pytest.mark.parametrize("missing", [False, True])
def test_invalid_public_standardization_rejects_without_normalization(
    analysis_dsl_case_factory: DslCaseFactory, missing: bool
) -> None:
    import duckdb

    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    if not missing:
        with duckdb.connect(str(case.database_path)) as db:
            db.execute('UPDATE "order" SET amount=-30 WHERE customer_id=?', ["C"])
    members = case.session.members(ms.ref.entity("sales.customer"))
    category = members.read(ms.ref.dimension("sales.customer.region"))
    values = (
        members.observe(
            ms.ref.metric("sales.revenue"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        )
        .group_by(category)
        .rollup()
    )
    weights = values.share_of(values.rollup())
    if missing:
        weights = weights.where(weights.value.gt(0))
    for current, composition, strata in (
        (values, weights, category),
        (values.execute(), weights.execute(), category.execute()),
    ):
        reference = mv.reference_weights(
            composition, strata=(strata,), unit=ms.ref.entity("sales.order")
        )
        with pytest.raises(AnalysisError):
            current.standardize(reference=reference).execute()


@pytest.mark.runtime
def test_fixed_selected_original_can_use_retained_full_reference(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    values = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    ).execute()
    reference = values.rollup().execute()
    selected = values.where(values.value.gt(0)).execute()
    result = selected.share_of(reference).execute()
    assert result.to_pandas().set_index("member").value.to_dict() == {
        "A": float(Fraction(1, 3)),
        "B": float(Fraction(2, 3)),
    }
    assert result._dataset.verified().parts[0].table["value"].to_pylist() == [180]


@pytest.mark.runtime
def test_reference_preparation_captures_independent_source_origins(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.errors import AnalysisError
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j3")
    members = case.session.members(ms.ref.entity("sales.customer"))
    categories = members.read(ms.ref.dimension("sales.customer.region"))
    scope = mv.time_scope(start="2026-08-01", end="2026-09-01")
    counts = (
        members.observe(
            ms.ref.metric("sales.order_count"),
            during=scope,
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        )
        .group_by(categories)
        .rollup()
    )
    lines = (
        members.observe(
            ms.ref.metric("sales.line_revenue"),
            during=scope,
            via=(
                mv.path(
                    ms.ref.relationship("sales.line_order"),
                    ms.ref.relationship("sales.order_buyer"),
                ),
            ),
            by=(mv.member(),),
        )
        .group_by(categories)
        .rollup()
    )
    weights = lines.share_of(lines.rollup())
    result = counts.standardize(
        reference=mv.reference_weights(
            weights, strata=(categories,), unit=ms.ref.entity("sales.order")
        )
    ).execute()
    assert result.to_pandas().value.tolist() == [float(len(analysis_dsl_rows("j3").orders))]
    assert {part.role for part in result._dataset.verified().parts} == {
        "fixed_reference",
        "reference_proof",
        "stratum_values",
        "strata",
    }
    routes = (
        mv.path(ms.ref.relationship("sales.order_buyer")),
        mv.path(ms.ref.relationship("sales.line_order"), ms.ref.relationship("sales.order_buyer")),
    )
    average = (
        members.observe(
            ms.ref.metric(f"sales.{case.names.aov}"),
            during=scope,
            via=(*reversed(routes),),
            by=(mv.member(),),
        )
        .group_by(categories)
        .rollup()
    )
    raw = analysis_dsl_rows("j3")
    expected = float(Fraction(sum(amount for _, _, amount in raw.lines), len(raw.orders)))
    for current, composition, classification in (
        (average, weights, categories),
        (average.execute(), weights.execute(), categories.execute()),
    ):
        standardized = current.standardize(
            reference=mv.reference_weights(
                composition, strata=(classification,), unit=ms.ref.entity("sales.order")
            )
        ).execute()
        assert standardized.to_pandas().value.tolist() == [expected]
    linear = (
        members.observe(
            mv.runtime_metric.linear(
                add=[ms.ref.metric("sales.revenue"), ms.ref.metric("sales.line_revenue")],
                label="different contributions",
            ),
            during=scope,
            via=routes,
            by=(mv.member(),),
        )
        .group_by(categories)
        .rollup()
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("unproved statistical units must reject before business reads")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    assert any("share_of(" in action.call for action in linear.contract().actions)
    assert not any("standardize(" in action.call for action in linear.contract().actions)
    for current, unit in ((average, "sales.order_line"), (linear, "sales.order")):
        with pytest.raises(AnalysisError):
            current.standardize(
                reference=mv.reference_weights(
                    weights, strata=(categories,), unit=ms.ref.entity(unit)
                )
            )


@pytest.mark.parametrize(
    "cell",
    [Defined(1e12), Null("empty_contribution"), Undefined("zero_denominator"), Unknown("coverage")],
)
def test_zero_weight_error_propagation(cell: object) -> None:
    from marivo.analysis.methods.references import standardized_error

    assert isinstance(cell, (Defined, Null, Undefined, Unknown))
    bound = standardized_error((Defined(1.0), cell), (1.0, 0.0), (0.0, 2.0), (0.0, 1e-6), 1.0)
    expected = (
        float(Fraction(1e12) * Fraction(1e-6) + Fraction(2) * Fraction(1e-6))
        if isinstance(cell, Defined)
        else 0.0
    ) + 2e-12
    assert bound == expected


@pytest.mark.parametrize("damage", [None, "identity", "duplicate", "null", "reference_duplicate"])
def test_penetration_proof_uses_complete_keys(
    damage: Literal["identity", "duplicate", "null", "reference_duplicate"] | None,
) -> None:
    import pyarrow as pa

    from marivo.analysis.core.model import Binding, DomainSignature
    from marivo.analysis.core.rules import ReferenceDerive
    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.graph_exchange import ExchangePart
    from marivo.analysis.materialization.graph_reference import finish

    schema = pa.schema(
        [
            pa.field("key_0", pa.string()),
            pa.field("key_1", pa.int64()),
            pa.field("key_2", pa.int64()),
        ]
    )
    members = pa.Table.from_pylist(
        [
            {"key_0": "a", "key_1": 9007199254740992, "key_2": 1},
            {"key_0": "a", "key_1": 9007199254740993, "key_2": 2},
            {"key_0": "b", "key_1": 9007199254740993, "key_2": 1},
        ],
        schema=schema,
    )
    values = members.take([0, 1])
    reference = members.cast(
        pa.schema([pa.field(field.name, field.type, nullable=False) for field in schema])
    )
    if damage == "null":
        values = values.set_column(
            0, schema.field("key_0"), pa.array(["a", None], type=pa.string())
        )
    elif damage == "reference_duplicate":
        reference = reference.take([0, 1, 2, 2])
    proof = values.take([1, 0])
    if damage == "identity":
        proof = proof.set_column(
            1,
            schema.field("key_1"),
            pa.array([9007199254740992, 9007199254740992], type=pa.int64()),
        )
    elif damage == "duplicate":
        proof = proof.take([0, 1, 1])
    domain = DomainSignature(Binding("proof", "sales", "facts", "all"), "singleton", (), (), "all")
    params = ReferenceDerive("penetration", domain, "members", None, "untimed")
    parts = (
        ExchangePart("stratum_values", values),
        ExchangePart("reference_proof", proof),
        ExchangePart("fixed_reference", reference),
    )
    if damage is None:
        assert finish(params, ScalarType("float64"), parts)["value"].to_pylist() == [
            float(Fraction(2, 3))
        ]
    else:
        with pytest.raises(AnalysisError, match=r"complete identities|intersection proof differs"):
            finish(params, ScalarType("float64"), parts)
