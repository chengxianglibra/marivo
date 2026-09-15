"""Source-only identity encoding, frozen evaluation and pre-limit validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import ibis
import ibis.expr.datatypes as dt
import pytest

from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.entity_candidate import entity_item_id, lower_entity_candidate
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.nodes import CompiledRelationFence, CompiledValidation
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.predicates import gt
from marivo.analysis.operators.candidate_contracts import CandidatePayload, CandidateSpecV1
from marivo.analysis.operators.registry import admit_local, implementation
from marivo.refs import ref
from tests.lazy_execution_fixtures import execution_fixture
from tests.lazy_observation_fixtures import make_sources


def _spec(*, limit: int = 50) -> CandidateSpecV1:
    dataset = (
        make_sources()
        .observe(ref.metric("sales.revenue"))
        .discover.entity_outliers(threshold=0.1, limit=limit)
    )
    assert isinstance(dataset._root, LogicalRootHandle)
    assert isinstance(dataset._root.payload, CandidatePayload)
    return dataset._root.payload.spec


def _identity_spec(signature: tuple[tuple[str, str], ...]) -> CandidateSpecV1:
    spec = _spec()

    def row(value: d.DatasetRowContract) -> d.DatasetRowContract:
        fields = []
        for field in value.schema.columns:
            if isinstance(field.identity, d._EntityFieldIdentity):
                identity = replace(
                    field.identity, _token=d._CORE_TOKEN, identity_signature=signature
                )
                field = replace(
                    field,
                    _token=d._CORE_TOKEN,
                    identity=identity,
                    logical_type_id=str(dt.Struct.from_tuples(signature)),
                )
            fields.append(field)
        return replace(value, _token=d._CORE_TOKEN, schema=d._make_schema(tuple(fields)))

    return replace(spec, input_row=row(spec.input_row), output_row=row(spec.output_row))


def test_ordered_native_identity_hash_matches_independent_json_digest() -> None:
    signature = (
        ("tenant", "string"),
        ("number", "float64"),
        ("day", "date"),
        ("stamp", "timestamp"),
        ("active", "boolean"),
    )
    spec = _identity_spec(signature)
    backend = ibis.duckdb.connect()
    try:
        values = {
            "tenant": ['\u5546\u6237 "\u96ea"', '\u5546\u6237 "\u96ea"'],
            "number": [-0.0, 0.0],
            "day": [date(2026, 2, 1)] * 2,
            "stamp": [datetime(2026, 2, 1, 1, 2, 3)] * 2,
            "active": [True] * 2,
        }
        raw = backend.create_table("identities", values)
        table = raw.select(entity_identity=ibis.struct({name: raw[name] for name, _ in signature}))
        expression = entity_item_id(table, spec.output_row, spec.definition)
        actual = backend.execute(table.select(item_id=expression)).item_id.tolist()
        identity_json = json.dumps(
            {
                "tenant": '\u5546\u6237 "\u96ea"',
                "number": 0.0,
                "day": "2026-02-01",
                "stamp": "2026-02-01 01:02:03",
                "active": True,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        preimage = (
            "candidate_entity_item@v1:"
            + d._canonical_digest(spec.definition.identity_payload())
            + ":"
            + d._canonical_digest(signature)
            + ":"
            + identity_json
        )
        expected = "sha256:" + hashlib.sha256(preimage.encode()).hexdigest()
        assert actual == [expected, expected]
        sql = backend.compile(table.select(item_id=expression))
        assert "SHA256" in sql and " AS JSON" in sql
        assert "PYTHON" not in sql.upper()
    finally:
        backend.disconnect()


@pytest.mark.parametrize("duplicate", [True, False])
def test_identity_and_digest_uniqueness_are_checked_before_limit(
    monkeypatch: pytest.MonkeyPatch, duplicate: bool
) -> None:
    backend = ibis.duckdb.connect()
    try:
        raw = backend.create_table(
            "identities", {"id": [1, 1 if duplicate else 2, 3, 4], "revenue": [1.0, 2.0, 3.0, 10.0]}
        )
        table = raw.select(entity_identity=ibis.struct({"id": raw.id}), revenue=raw.revenue)
        if not duplicate:
            monkeypatch.setattr(
                "marivo.analysis.compiler.entity_candidate.entity_item_id",
                lambda *_args: ibis.literal("sha256:forced-collision"),
            )
        _, checks, _ = lower_entity_candidate(table, _spec(limit=1))
        name = (
            "candidate.entity_identity_unique" if duplicate else "candidate.entity_item_id_unique"
        )
        check = next(check for check in checks if check.name == name)
        assert backend.execute(check.expression).iloc[0, 0] == 1
    finally:
        backend.disconnect()


def test_entity_candidate_and_selection_have_no_local_implementation() -> None:
    candidate = make_sources().observe(ref.metric("sales.revenue")).discover.entity_outliers()
    selected = candidate.where(gt(candidate.fields.get("score"), 4.0)).limit(1)
    for dataset in (candidate, selected):
        registration = implementation(dataset)
        assert (
            tuple(item.backend for item in registration.backends) == ("duckdb",)
            and registration.local_method is None
        )
        with pytest.raises(DatasetCompilationError, match="source-required"):
            admit_local(dataset, registration)


def test_native_scoring_validations_and_proof_share_one_frozen_input(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        candidate = fixture.sources.observe(ref.metric("sales.revenue")).discover.entity_outliers(
            threshold=0.1
        )
        compiled = compile_dataset(candidate, fixture.tables(candidate))
        fences = [step for step in compiled.preparations if isinstance(step, CompiledRelationFence)]
        assert len(fences) == 1
        for preparation in compiled.preparations:
            if isinstance(preparation, CompiledRelationFence):
                fixture.backend.create_table(
                    preparation.relation_name, preparation.expression, temp=True
                )
            else:
                assert isinstance(preparation, CompiledValidation)
                assert fixture.backend.execute(preparation.expression).iloc[0, 0] == 0
        assert compiled.candidate_proof is not None
        assert all("identity" not in column for column in compiled.candidate_proof.columns)
        original = fixture.backend.execute(compiled.expression)
        fixture.backend.raw_sql("DROP TABLE orders")
        assert fixture.backend.execute(compiled.expression).equals(original)
        assert fixture.backend.execute(compiled.candidate_proof).iloc[0].non_null_value_count == 5
