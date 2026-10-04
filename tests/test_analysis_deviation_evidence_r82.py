"""Independent authority gates for requirement closure; fixtures are not qualification."""

from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.methods.physical import (
    NoTime,
    QualificationKey,
    ScalarType,
    SourceShape,
    TimeShape,
)
from marivo.analysis.methods.semantics import MethodKey
from scripts.r81_static_freeze import (
    SNAPSHOT,
    Json,
    array_json,
    decode_payload,
    object_json,
    read_json,
)
from scripts.r82_deviation_evidence import KernelProof
from scripts.r82_deviation_requirements import match, match_chain
from scripts.r82_f11_evidence import SourceChainProof


@pytest.fixture(scope="module")
def frozen_requirement() -> dict[str, Json]:
    frozen = decode_payload(Path.cwd(), read_json(Path(SNAPSHOT)))
    return next(
        object_json(row)
        for row in array_json(frozen["requirements"])
        if object_json(row)["id"] == "Q-R8-INTEGRATED-KT-deviation-zscore-int64-scalar-TABLE-NONE-S"
    )


def _proof() -> KernelProof:
    key = QualificationKey(
        MethodKey("deviation.zscore"),
        (ScalarType("int64"),),
        ("singleton",),
        SourceShape("duckdb", "table", "native", NoTime()),
        "ibis_python",
    )
    return KernelProof(
        origin=key,
        actual=key,
        implementation_id="r8-deviation-zscore-ibis_python-v1",
        contract_version=1,
        precision_contract="certified_statistical",
        state_version="v1",
        numeric_policy="r8_numeric_v1",
        input_codec="r8.fit_inputs/v1",
        state_codec="r8.fit_state/v1",
        selection_transform="select_output_retain_scope@v1",
        domain="scalar",
        key_profile="KT",
        time_profile="none",
        proof_class="source_kernel",
        artifact_ref="fixture_artifact",
        producing_run_ref="fixture_run",
        execution_key="0" * 64,
        descriptor_digest="1" * 64,
        primary_receipt_digest="2" * 64,
        part_receipt_digests=tuple(
            (role, "3" * 64) for role in ("fit_inputs", "fit_state", "finding_policy")
        ),
        input_digest="4" * 64,
        fit_scope_rows=1,
        fit_scope_digest="5" * 64,
        fit_state_digest="6" * 64,
        current_key_fields=(),
    )


@pytest.mark.parametrize(
    "damage",
    (
        "method",
        "carrier",
        "route",
        "origin",
        "origin_method",
        "origin_carrier",
        "origin_domain",
        "origin_precision",
        "implementation",
        "contract_version",
        "precision",
        "state_version",
        "numeric_policy",
        "input_codec",
        "state_codec",
        "transform",
        "domain",
        "key_profile",
        "time_profile",
        "proof_class",
        "part",
    ),
)
def test_each_authority_fact_is_required_after_clean_match(
    frozen_requirement: dict[str, Json], damage: str
) -> None:
    proof = _proof()
    assert match(frozen_requirement, proof)
    if damage == "method":
        proof = replace(proof, actual=replace(proof.actual, method=MethodKey("deviation.mad")))
    elif damage == "carrier":
        proof = replace(proof, actual=replace(proof.actual, input_types=(ScalarType("float64"),)))
    elif damage == "route":
        proof = replace(proof, actual=replace(proof.actual, route="ibis"))
    elif damage == "origin":
        proof = replace(
            proof,
            origin=replace(
                proof.origin, shape=SourceShape("duckdb", "parquet", "parquet", NoTime())
            ),
        )
    elif damage == "origin_method":
        proof = replace(proof, origin=replace(proof.origin, method=MethodKey("deviation.mad")))
    elif damage == "origin_carrier":
        proof = replace(proof, origin=replace(proof.origin, input_types=(ScalarType("float64"),)))
    elif damage == "origin_domain":
        proof = replace(proof, origin=replace(proof.origin, input_domains=("group",)))
    elif damage == "origin_precision":
        proof = replace(
            proof,
            origin=replace(
                proof.origin,
                shape=SourceShape("duckdb", "table", "native", TimeShape("instant", "us", "UTC")),
            ),
        )
    elif damage == "implementation":
        proof = replace(proof, implementation_id="different_implementation")
    elif damage == "contract_version":
        proof = replace(proof, contract_version=2)
    elif damage == "precision":
        proof = replace(proof, precision_contract="exact")
    elif damage == "state_version":
        proof = replace(proof, state_version="v2")
    elif damage == "numeric_policy":
        proof = replace(proof, numeric_policy="different_policy")
    elif damage == "input_codec":
        proof = replace(proof, input_codec="r8.fit_inputs/v2")
    elif damage == "state_codec":
        proof = replace(proof, state_codec="r8.fit_state/v2")
    elif damage == "transform":
        proof = replace(proof, selection_transform="refit_selected_rows")
    elif damage == "domain":
        proof = replace(proof, domain="entity")
    elif damage == "key_profile":
        proof = replace(proof, key_profile="KS")
    elif damage == "time_profile":
        proof = replace(proof, time_profile="builtin_day:grid_us:UTC")
    elif damage == "proof_class":
        proof = replace(proof, proof_class="fresh_process_source_offline_kernel")
    else:
        proof = replace(proof, part_receipt_digests=proof.part_receipt_digests[:-1])
    assert not match(frozen_requirement, proof)


@pytest.fixture(scope="module")
def frozen_chain_requirement() -> dict[str, Json]:
    frozen = decode_payload(Path.cwd(), read_json(Path(SNAPSHOT)))
    return next(
        object_json(row)
        for row in array_json(frozen["requirements"])
        if object_json(row)["id"] == "Q-R8-F11-KS-deviation-zscore-int64-entity-TABLE-NONE-S"
    )


def _chain_proof() -> SourceChainProof:
    key = QualificationKey(
        MethodKey("deviation.zscore"),
        (ScalarType("int64"),),
        ("entity",),
        SourceShape("duckdb", "table", "native", NoTime()),
        "ibis_python",
    )
    return SourceChainProof(
        actual=key,
        implementation_id="r8-deviation-zscore-ibis_python-v1",
        contract_version=1,
        precision_contract="certified_statistical",
        state_version="v1",
        numeric_policy="r8_numeric_v1",
        input_codec="r8.fit_inputs/v1",
        state_codec="r8.fit_state/v1",
        selection_transform="select_output_retain_scope@v1",
        domain="entity",
        key_profile="KS",
        time_profile="none",
        contribution_type="int64",
        followup_reduction="sum",
        summary_reduction="count_defined",
        followup_artifact="fixture_followup",
        summary_artifact="fixture_summary",
        producing_run_ref="fixture_run",
        execution_key="0" * 64,
        followup_receipt_digest="1" * 64,
        summary_receipt_digest="2" * 64,
        fit_scope="fixture_fit",
        input_digest="3" * 64,
        consumed_fit_part_digests=tuple(
            (role, "4" * 64)
            for role in ("fit_inputs", "fit_state", "finding_policy", "subject_map")
        ),
        source_read_count=2,
        local_count=1,
        last_source_read=1,
        first_local_consume=2,
        manifest_digest="5" * 64,
    )


@pytest.mark.parametrize(
    "damage",
    (
        "carrier",
        "route",
        "origin",
        "implementation",
        "contract_version",
        "precision",
        "state_version",
        "numeric_policy",
        "input_codec",
        "state_codec",
        "transform",
        "domain",
        "key_profile",
        "time_profile",
        "part",
        "followup_reduction",
        "contribution_type",
        "summary_reduction",
        "no_source_read",
        "duplicate_fit",
        "late_source_read",
    ),
)
def test_source_chain_matcher_never_promotes_other_obligations(
    frozen_chain_requirement: dict[str, Json], damage: str
) -> None:
    proof = _chain_proof()
    assert match_chain(frozen_chain_requirement, proof)
    if damage == "carrier":
        proof = replace(proof, actual=replace(proof.actual, input_types=(ScalarType("float64"),)))
    elif damage == "route":
        proof = replace(proof, actual=replace(proof.actual, route="ibis"))
    elif damage == "origin":
        proof = replace(
            proof,
            actual=replace(
                proof.actual, shape=SourceShape("duckdb", "parquet", "parquet", NoTime())
            ),
        )
    elif damage == "implementation":
        proof = replace(proof, implementation_id="different_implementation")
    elif damage == "contract_version":
        proof = replace(proof, contract_version=2)
    elif damage == "precision":
        proof = replace(proof, precision_contract="exact")
    elif damage == "state_version":
        proof = replace(proof, state_version="v2")
    elif damage == "numeric_policy":
        proof = replace(proof, numeric_policy="different_policy")
    elif damage == "input_codec":
        proof = replace(proof, input_codec="r8.fit_inputs/v2")
    elif damage == "state_codec":
        proof = replace(proof, state_codec="r8.fit_state/v2")
    elif damage == "transform":
        proof = replace(proof, selection_transform="refit_selected_rows")
    elif damage == "domain":
        proof = replace(proof, domain="scalar")
    elif damage == "key_profile":
        proof = replace(proof, key_profile="KC")
    elif damage == "time_profile":
        proof = replace(proof, time_profile="builtin_day:grid_us:UTC")
    elif damage == "part":
        proof = replace(proof, consumed_fit_part_digests=proof.consumed_fit_part_digests[:-1])
    elif damage == "followup_reduction":
        proof = replace(proof, followup_reduction="mean")
    elif damage == "contribution_type":
        proof = replace(proof, contribution_type="decimal(18,6)")
    elif damage == "summary_reduction":
        proof = replace(proof, summary_reduction="sum")
    elif damage == "no_source_read":
        proof = replace(proof, source_read_count=0)
    elif damage == "duplicate_fit":
        proof = replace(proof, local_count=2)
    else:
        proof = replace(proof, last_source_read=proof.first_local_consume)
    assert not match_chain(frozen_chain_requirement, proof)


@pytest.mark.parametrize("carrier", ("int64", "timestamp[us, tz=UTC]"))
def test_time_requirement_matches_only_its_actual_captured_cell_key(carrier: str) -> None:
    frozen = decode_payload(Path.cwd(), read_json(Path(SNAPSHOT)))
    row = next(
        object_json(row)
        for row in array_json(frozen["requirements"])
        if object_json(row)["id"] == "Q-R8-INTEGRATED-KT-deviation-zscore-int64-time-TABLE-US-UTC-S"
    )
    key = replace(
        _proof().actual,
        input_domains=("group",),
        shape=SourceShape("duckdb", "table", "native", TimeShape("instant", "us", "UTC")),
    )
    proof = replace(
        _proof(),
        origin=key,
        actual=key,
        domain="time",
        time_profile="builtin_day:grid_us:UTC",
        current_key_fields=(("key_0", "string"),),
        part_receipt_digests=(*_proof().part_receipt_digests, ("grid_cells", "7" * 64)),
    )
    assert match(row, proof)
    assert not match(row, replace(proof, current_key_fields=(("key_0", carrier),)))
