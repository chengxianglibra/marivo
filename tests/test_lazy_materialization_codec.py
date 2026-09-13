"""Closed v3 values recover contracts without loading source authority or rows."""

from dataclasses import replace

import pytest

from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.contracts import (
    FileEntry,
    canonical_json,
    decode_descriptor,
    decode_receipt,
    descriptor_payload,
    encode_descriptor,
    parse_json,
    receipt_payload,
)
from marivo.analysis.materialization.errors import IntegrityError
from tests.lazy_materialization_fixtures import descriptor


@pytest.mark.parametrize("payload", ['{"private-value-canary":', "[" * 2000, "NaN"])
def test_invalid_json_discards_native_parser_context(payload: str) -> None:
    with pytest.raises(IntegrityError) as caught:
        parse_json(payload)
    assert "private-value-canary" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_invalid_json_encoding_discards_native_context() -> None:
    with pytest.raises(IntegrityError) as caught:
        canonical_json(float("nan"))
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_descriptor_round_trip_retains_exact_core_contracts_without_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = descriptor()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("cold metadata decoding must not load semantic or data authority")

    monkeypatch.setattr("marivo.semantic.validator.Registry.__init__", forbidden)
    monkeypatch.setattr("pyarrow.parquet.ParquetFile", forbidden)
    encoded = encode_descriptor(value)
    recovered = decode_descriptor(encoded)
    assert encode_descriptor(recovered) == encoded
    assert recovered.row_contract_fingerprint == value.row_contract_fingerprint
    assert recovered.row_set_contract_fingerprint == value.row_set_contract_fingerprint
    assert recovered.realized_schema_fingerprint == value.realized_schema_fingerprint
    assert recovered.storage_receipt.identity_digest == value.storage_receipt.identity_digest


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "marivo.dataset_artifact_descriptor/v2"),
        ("schema", "analysis-artifact/v13"),
        ("row_contract_fingerprint", "0" * 64),
        ("row_set_contract_fingerprint", "0" * 64),
        ("realized_schema_fingerprint", "0" * 64),
        ("sampling_execution", {"kind": "invented"}),
        ("origin_graph", {"sql": "SELECT * FROM source"}),
    ],
)
def test_descriptor_rejects_unknown_or_inconsistent_authority(field: str, value: object) -> None:
    payload = descriptor_payload(descriptor())
    payload[field] = value
    with pytest.raises(DatasetConstructionError):
        decode_descriptor(canonical_json(payload))


def test_unknown_materialization_registration_is_not_cold_read_authority() -> None:
    value = descriptor()
    contract = replace(value.dataset_materialization_contract, evidence_extractor_version=2)
    with pytest.raises(IntegrityError, match="unregistered materialization"):
        decode_descriptor(
            encode_descriptor(replace(value, dataset_materialization_contract=contract))
        )


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a/../b", "a//b", "a\\b"])
def test_receipt_paths_fail_closed(path: str) -> None:
    with pytest.raises(IntegrityError):
        FileEntry(path, 8, "a" * 64)


def test_receipt_is_closed_and_counts_are_exact() -> None:
    receipt = descriptor().storage_receipt
    payload = receipt_payload(receipt)
    payload["realized_byte_count"] = {"kind": "unavailable", "reason": "unknown"}
    with pytest.raises(IntegrityError):
        decode_receipt(payload)
    with pytest.raises(IntegrityError):
        replace(receipt, realized_row_count=True)
    with pytest.raises(IntegrityError):
        replace(receipt, realized_byte_count=0)
    with pytest.raises(IntegrityError):
        replace(receipt, manifest_hash="b" * 64)


def test_json_duplicates_and_noncanonical_values_are_rejected() -> None:
    encoded = encode_descriptor(descriptor())
    with pytest.raises(IntegrityError):
        decode_descriptor(encoded.replace("{", '{"schema":"untrusted",', 1))
    with pytest.raises(IntegrityError):
        decode_descriptor(encoded + "\n")


def test_registered_metric_parts_are_complete_per_metric() -> None:
    value = descriptor(metric=True)
    assert len(value.retained_parts) == 2
    assert encode_descriptor(decode_descriptor(encode_descriptor(value))) == encode_descriptor(
        value
    )
    with pytest.raises(IntegrityError, match="roles mismatch"):
        decode_descriptor(
            encode_descriptor(replace(value, retained_parts=value.retained_parts[:1]))
        )
    part = value.retained_parts[0]
    wrong_count = replace(
        part, storage_receipt=replace(part.storage_receipt, realized_row_count=99)
    )
    with pytest.raises(IntegrityError, match="contract or count"):
        decode_descriptor(
            encode_descriptor(
                replace(value, retained_parts=(wrong_count, *value.retained_parts[1:]))
            )
        )


@pytest.mark.parametrize("population", ["snapshots", "validity"])
def test_versioned_population_retains_exact_selection_facts(population: str) -> None:
    original = descriptor(population=population)
    recovered = decode_descriptor(encode_descriptor(original))
    assert recovered.population_authority == original.population_authority
    assert recovered.population_authority.membership_scope is not None
    assert recovered.population_authority.version_selection is not None
    assert recovered.population_authority.validation_results == (("identity_uniqueness", 0),)
