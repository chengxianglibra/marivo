"""On-disk persistence for authoring evidence metadata."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from marivo.semantic.evidence import (
    AssessmentIssue,
    AuthoringEvidenceInput,
    ColumnEvidence,
    ColumnProfile,
    DatasetSource,
    EvidenceKind,
    EvidenceRef,
    IssueKind,
    NextCheck,
    SamplePolicy,
    Severity,
    SourceEvidencePack,
)
from marivo.semantic.ir import source_label

JsonRecord = dict[str, object]
_EVIDENCE_ID_PATTERN = re.compile(r"^(src|col|doc):[0-9a-f]{16}$")


def _hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def structural_fingerprint(
    datasource: str,
    source: DatasetSource,
    schema: Sequence[tuple[str, str]],
    table_comment: str | None,
    column_comments: Sequence[tuple[str, str]],
) -> str:
    payload = {
        "datasource": datasource,
        "source": source.to_dict(),
        "schema": sorted(schema),
        "table_comment": table_comment,
        "column_comments": sorted(column_comments),
    }
    return f"sha256:{_hash(_canonical_json(payload))}"


def content_fingerprint(content: str) -> str:
    return f"sha256:{_hash(content)}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _source_evidence_id(datasource: str, source: DatasetSource, structural_fp: str) -> str:
    return f"src:{_hash(datasource, source_label(source.to_ir()), structural_fp)[:16]}"


def _column_evidence_id(
    datasource: str,
    source: DatasetSource,
    column: str,
    structural_fp: str,
) -> str:
    return f"col:{_hash(datasource, source_label(source.to_ir()), column, structural_fp)[:16]}"


class EvidenceStore:
    def __init__(self, semantic_root: str | Path) -> None:
        self.semantic_root = Path(semantic_root)
        self.root = self.semantic_root / ".evidence"
        self._ref_cache: dict[str, EvidenceRef] = {}

    def _packs_dir(self) -> Path:
        return self.root / "packs"

    def _authoring_dir(self) -> Path:
        return self.root / "authoring"

    def make_source_ref(
        self,
        datasource: str,
        source: DatasetSource,
        structural_fp: str,
        collected_at: str,
    ) -> EvidenceRef:
        evidence_id = _source_evidence_id(datasource, source, structural_fp)
        ref = EvidenceRef(
            id=evidence_id,
            kind="catalog_metadata",
            datasource=datasource,
            source=source,
            collected_at=collected_at,
            structural_fingerprint=structural_fp,
        )
        self._ref_cache[ref.id] = ref
        return ref

    def make_column_ref(
        self,
        datasource: str,
        source: DatasetSource,
        column: str,
        structural_fp: str,
        collected_at: str,
    ) -> EvidenceRef:
        evidence_id = _column_evidence_id(datasource, source, column, structural_fp)
        ref = EvidenceRef(
            id=evidence_id,
            kind="schema",
            datasource=datasource,
            source=source,
            collected_at=collected_at,
            structural_fingerprint=structural_fp,
        )
        self._ref_cache[ref.id] = ref
        return ref

    def write_source_pack(self, pack: SourceEvidencePack) -> None:
        if not pack.evidence_refs:
            raise ValueError("source evidence pack requires at least one evidence ref")
        expected_fp = _source_pack_structural_fingerprint(pack)
        for ref in pack.evidence_refs:
            _validate_source_pack_ref(pack, ref, expected_fp)
        primary_ref = pack.evidence_refs[0]
        pack_id = primary_ref.id
        _validate_evidence_id(pack_id, ("src",), field="source evidence id")
        record = _source_pack_to_record(pack)
        self._write_json(self._packs_dir() / f"{pack_id}.json", record)

    def write_column_evidence(self, column_evidence: ColumnEvidence) -> None:
        if not column_evidence.evidence_refs:
            raise ValueError("column evidence requires at least one evidence ref")
        if column_evidence.profile.column != column_evidence.column:
            raise ValueError("column evidence profile column must match column evidence column")
        evidence_refs = tuple(
            self._cached_column_ref(ref_id) for ref_id in column_evidence.evidence_refs
        )
        for ref in evidence_refs:
            _validate_column_evidence_ref(column_evidence, ref)
        pack_id = evidence_refs[0].id
        record = _column_evidence_to_record(column_evidence, evidence_refs)
        self._write_json(self._packs_dir() / f"{pack_id}.json", record)

    def write_authoring_evidence(self, evidence: AuthoringEvidenceInput) -> EvidenceRef:
        fingerprint = evidence.content_fingerprint or content_fingerprint(evidence.content)
        subject_refs = tuple(sorted(evidence.subject_refs))
        evidence_id = f"doc:{_hash(evidence.kind, *subject_refs, fingerprint)[:16]}"
        ref = EvidenceRef(
            id=evidence_id,
            kind=evidence.kind,
            datasource=None,
            source=None,
            collected_at=_now(),
            content_fingerprint=fingerprint,
        )
        _validate_evidence_id(evidence_id, ("doc",), field="authoring evidence id")
        record: JsonRecord = {
            "record_type": "authoring",
            "id": evidence_id,
            "ref": ref.to_dict(),
            "kind": evidence.kind,
            "subject_refs": list(subject_refs),
            "content": evidence.content,
            "source_document": evidence.source_document,
            "source_dialect": evidence.source_dialect,
            "content_fingerprint": fingerprint,
        }
        self._write_json(self._authoring_dir() / f"{evidence_id}.json", record)
        return ref

    def read_pack(self, evidence_id: str) -> SourceEvidencePack | ColumnEvidence | None:
        if not _is_valid_evidence_id(evidence_id, ("src", "col")):
            return None
        path = self._packs_dir() / f"{evidence_id}.json"
        if not path.exists():
            return None
        record = _read_json(path)
        record_type = record.get("record_type")
        if record_type == "source":
            return _source_pack_from_record(record)
        if record_type == "column":
            return _column_evidence_from_record(record)
        raise ValueError(f"unsupported evidence pack record type: {record_type!r}")

    def read_authoring(self, evidence_id: str) -> tuple[EvidenceRef, str] | None:
        if not _is_valid_evidence_id(evidence_id, ("doc",)):
            return None
        path = self._authoring_dir() / f"{evidence_id}.json"
        if not path.exists():
            return None
        record = _read_json(path)
        ref_data = _mapping(record["ref"], "ref")
        return _evidence_ref_from_dict(ref_data), _str(record["content"], "content")

    def list_evidence(
        self,
        datasource: str | None = None,
        source: DatasetSource | None = None,
        subject_refs: Sequence[str] | None = None,
    ) -> tuple[EvidenceRef, ...]:
        if subject_refs is not None:
            subjects = set(subject_refs)
            refs: list[EvidenceRef] = []
            for record in self._sorted_json(self._authoring_dir()):
                record_subjects = set(_str_tuple(record.get("subject_refs", ()), "subject_refs"))
                if subjects.intersection(record_subjects):
                    refs.append(_evidence_ref_from_dict(_mapping(record["ref"], "ref")))
            return tuple(refs)

        if datasource is not None and source is not None:
            refs = []
            source_data = source.to_dict()
            for record in self._sorted_json(self._packs_dir()):
                if record.get("datasource") != datasource or record.get("source") != source_data:
                    continue
                refs.extend(_record_refs(record))
            return tuple(refs)

        return ()

    def list_authoring_by_kind(self, kind: str) -> tuple[EvidenceRef, ...]:
        refs: list[EvidenceRef] = []
        for record in self._sorted_json(self._authoring_dir()):
            if record.get("kind") != kind:
                continue
            ref_data = _mapping(record["ref"], "ref")
            refs.append(_evidence_ref_from_dict(ref_data))
        return tuple(refs)

    def _sorted_json(self, directory: Path) -> tuple[JsonRecord, ...]:
        if not directory.exists():
            return ()
        return tuple(_read_json(path) for path in sorted(directory.glob("*.json")))

    def _write_json(self, path: Path, record: JsonRecord) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_canonical_json(record) + "\n", encoding="utf-8")

    def _cached_column_ref(self, ref_id: str) -> EvidenceRef:
        _validate_evidence_id(ref_id, ("col",), field="column evidence ref id")
        ref = self._ref_cache.get(ref_id)
        if ref is None:
            raise ValueError(f"unknown column evidence ref id: {ref_id}")
        return ref


def _source_pack_to_record(pack: SourceEvidencePack) -> JsonRecord:
    return {
        "record_type": "source",
        "datasource": pack.datasource,
        "source": pack.source.to_dict(),
        "schema": [list(item) for item in pack.schema],
        "table_comment": pack.table_comment,
        "column_comments": [list(item) for item in pack.column_comments],
        "nullable": [list(item) for item in pack.nullable],
        "partition_hints": list(pack.partition_hints),
        "key_hints": [list(item) for item in pack.key_hints],
        "column_profiles": [profile.to_dict() for profile in pack.column_profiles],
        "metadata_warnings": list(pack.metadata_warnings),
        "evidence_refs": [ref.to_dict() for ref in pack.evidence_refs],
        "sample_policy": _sample_policy_to_dict(pack.sample_policy),
        "redaction_status": pack.redaction_status,
        "truncated": pack.truncated,
    }


def _source_pack_structural_fingerprint(pack: SourceEvidencePack) -> str:
    return structural_fingerprint(
        datasource=pack.datasource,
        source=pack.source,
        schema=pack.schema,
        table_comment=pack.table_comment,
        column_comments=pack.column_comments,
    )


def _validate_source_pack_ref(
    pack: SourceEvidencePack,
    ref: EvidenceRef,
    expected_structural_fingerprint: str,
) -> None:
    if ref.kind != "catalog_metadata":
        raise ValueError("source evidence ref kind must be catalog_metadata")
    if ref.datasource != pack.datasource:
        raise ValueError("source evidence ref datasource must match source pack datasource")
    if ref.source != pack.source:
        raise ValueError("source evidence ref source must match source pack source")
    if ref.structural_fingerprint != expected_structural_fingerprint:
        raise ValueError(
            "source evidence ref structural_fingerprint must match source pack payload"
        )
    expected_id = _source_evidence_id(
        pack.datasource,
        pack.source,
        expected_structural_fingerprint,
    )
    if ref.id != expected_id:
        raise ValueError("source evidence ref id does not match its deterministic id")


def _validate_column_evidence_ref(column_evidence: ColumnEvidence, ref: EvidenceRef) -> None:
    if ref.kind != "schema":
        raise ValueError("column evidence ref kind must be schema")
    if ref.datasource != column_evidence.datasource:
        raise ValueError("column evidence ref datasource must match column evidence datasource")
    if ref.source != column_evidence.source:
        raise ValueError("column evidence ref source must match column evidence source")
    if ref.structural_fingerprint is None:
        raise ValueError("column evidence ref structural_fingerprint is required")
    expected_id = _column_evidence_id(
        column_evidence.datasource,
        column_evidence.source,
        column_evidence.column,
        ref.structural_fingerprint,
    )
    if ref.id != expected_id:
        raise ValueError("column evidence ref id does not match its deterministic id")


def _column_evidence_to_record(
    column_evidence: ColumnEvidence,
    evidence_refs: Sequence[EvidenceRef],
) -> JsonRecord:
    return {
        "record_type": "column",
        "datasource": column_evidence.datasource,
        "source": column_evidence.source.to_dict(),
        "column": column_evidence.column,
        "profile": column_evidence.profile.to_dict(),
        "issues": [_assessment_issue_to_dict(issue) for issue in column_evidence.issues],
        "evidence_refs": [ref.to_dict() for ref in evidence_refs],
        "collected_at": _now(),
    }


def _source_pack_from_record(record: Mapping[str, object]) -> SourceEvidencePack:
    return SourceEvidencePack(
        datasource=_str(record["datasource"], "datasource"),
        source=_dataset_source_from_dict(_mapping(record["source"], "source")),
        schema=_pair_str_tuple(record["schema"], "schema"),
        table_comment=_optional_str(record.get("table_comment"), "table_comment"),
        column_comments=_pair_str_tuple(record["column_comments"], "column_comments"),
        nullable=_nullable_tuple(record["nullable"], "nullable"),
        partition_hints=_str_tuple(record["partition_hints"], "partition_hints"),
        key_hints=tuple(
            _str_tuple(item, "key_hints item")
            for item in _sequence(record["key_hints"], "key_hints")
        ),
        column_profiles=tuple(
            _column_profile_from_dict(_mapping(item, "column profile"))
            for item in _sequence(record["column_profiles"], "column_profiles")
        ),
        metadata_warnings=_str_tuple(record["metadata_warnings"], "metadata_warnings"),
        evidence_refs=tuple(
            _evidence_ref_from_dict(_mapping(item, "evidence ref"))
            for item in _sequence(record["evidence_refs"], "evidence_refs")
        ),
        sample_policy=_sample_policy_from_dict(_mapping(record["sample_policy"], "sample_policy")),
        redaction_status=cast(
            'Literal["redacted", "not_redacted"]',
            _str(record["redaction_status"], "redaction_status"),
        ),
        truncated=_bool(record["truncated"], "truncated"),
    )


def _column_evidence_from_record(record: Mapping[str, object]) -> ColumnEvidence:
    return ColumnEvidence(
        datasource=_str(record["datasource"], "datasource"),
        source=_dataset_source_from_dict(_mapping(record["source"], "source")),
        column=_str(record["column"], "column"),
        profile=_column_profile_from_dict(_mapping(record["profile"], "profile")),
        issues=tuple(
            _assessment_issue_from_dict(_mapping(item, "issue"))
            for item in _sequence(record.get("issues", ()), "issues")
        ),
        evidence_refs=tuple(
            _str(_mapping(item, "evidence ref")["id"], "evidence ref id")
            for item in _sequence(record["evidence_refs"], "evidence_refs")
        ),
    )


def _sample_policy_to_dict(policy: SamplePolicy) -> JsonRecord:
    return {
        "mode": policy.mode,
        "limit": policy.limit,
        "columns": list(policy.columns),
        "timeout_seconds": policy.timeout_seconds,
        "max_profiled_columns": policy.max_profiled_columns,
        "redact": policy.redact,
    }


def _sample_policy_from_dict(data: Mapping[str, object]) -> SamplePolicy:
    return SamplePolicy(
        mode=cast(
            'Literal["metadata_only", "bounded_profile", "selected_columns_profile"]',
            _str(data["mode"], "mode"),
        ),
        limit=_optional_int(data.get("limit"), "limit"),
        columns=_str_tuple(data.get("columns", ()), "columns"),
        timeout_seconds=_optional_int(data.get("timeout_seconds"), "timeout_seconds"),
        max_profiled_columns=_optional_int(
            data.get("max_profiled_columns"), "max_profiled_columns"
        ),
        redact=_bool(data.get("redact", True), "redact"),
    )


def _dataset_source_from_dict(data: Mapping[str, object]) -> DatasetSource:
    database_value = data.get("database")
    database: str | tuple[str, ...] | None
    if database_value is None:
        database = None
    elif isinstance(database_value, str):
        database = database_value
    else:
        database = _str_tuple(database_value, "database")

    return DatasetSource(
        kind=cast('Literal["table", "file"]', _str(data["kind"], "kind")),
        table=_optional_str(data.get("table"), "table"),
        database=database,
        path=_optional_str(data.get("path"), "path"),
        format=cast(
            'Literal["parquet", "csv"] | None', _optional_str(data.get("format"), "format")
        ),
    )


def _evidence_ref_from_dict(data: Mapping[str, object]) -> EvidenceRef:
    source_data = data.get("source")
    source = (
        None if source_data is None else _dataset_source_from_dict(_mapping(source_data, "source"))
    )
    return EvidenceRef(
        id=_str(data["id"], "id"),
        kind=cast("EvidenceKind", _str(data["kind"], "kind")),
        datasource=_optional_str(data.get("datasource"), "datasource"),
        source=source,
        collected_at=_str(data["collected_at"], "collected_at"),
        structural_fingerprint=_optional_str(
            data.get("structural_fingerprint"), "structural_fingerprint"
        ),
        content_fingerprint=_optional_str(data.get("content_fingerprint"), "content_fingerprint"),
    )


def _column_profile_from_dict(data: Mapping[str, object]) -> ColumnProfile:
    return ColumnProfile(
        column=_str(data["column"], "column"),
        data_type=_str(data["data_type"], "data_type"),
        nullable=_optional_bool(data.get("nullable"), "nullable"),
        comment=_optional_str(data.get("comment"), "comment"),
        null_count=_optional_int(data.get("null_count"), "null_count"),
        empty_count=_optional_int(data.get("empty_count"), "empty_count"),
        distinct_count=_optional_int(data.get("distinct_count"), "distinct_count"),
        top_values=tuple(
            _top_value(item) for item in _sequence(data.get("top_values", ()), "top_values")
        ),
        min_value=data.get("min_value"),
        max_value=data.get("max_value"),
        observed_formats=_str_tuple(data.get("observed_formats", ()), "observed_formats"),
        warnings=_str_tuple(data.get("warnings", ()), "warnings"),
        sample_scope=cast(
            'Literal["none", "bounded_sample"]',
            _str(data.get("sample_scope", "bounded_sample"), "sample_scope"),
        ),
        approximate=_bool(data.get("approximate", True), "approximate"),
    )


def _record_refs(record: Mapping[str, object]) -> tuple[EvidenceRef, ...]:
    record_type = record.get("record_type")
    if record_type == "source":
        return tuple(
            _evidence_ref_from_dict(_mapping(item, "evidence ref"))
            for item in _sequence(record.get("evidence_refs", ()), "evidence_refs")
        )
    if record_type == "column":
        return tuple(
            _evidence_ref_from_dict(_mapping(item, "evidence ref"))
            for item in _sequence(record.get("evidence_refs", ()), "evidence_refs")
        )
    return ()


def _is_valid_evidence_id(evidence_id: str, allowed_prefixes: tuple[str, ...]) -> bool:
    match = _EVIDENCE_ID_PATTERN.fullmatch(evidence_id)
    return match is not None and match.group(1) in allowed_prefixes


def _validate_evidence_id(
    evidence_id: str,
    allowed_prefixes: tuple[str, ...],
    *,
    field: str,
) -> None:
    if not _is_valid_evidence_id(evidence_id, allowed_prefixes):
        supported = ", ".join(f"{prefix}:<16 hex>" for prefix in allowed_prefixes)
        raise ValueError(
            f"{field} must use a generated evidence id ({supported}); got {evidence_id!r}"
        )


def _assessment_issue_to_dict(issue: AssessmentIssue) -> JsonRecord:
    return {
        "kind": issue.kind,
        "severity": issue.severity,
        "refs": list(issue.refs),
        "message": issue.message,
        "rule_id": issue.rule_id,
        "evidence_refs": list(issue.evidence_refs),
        "next_checks": list(issue.next_checks),
    }


def _assessment_issue_from_dict(data: Mapping[str, object]) -> AssessmentIssue:
    return AssessmentIssue(
        kind=cast("IssueKind", _str(data["kind"], "kind")),
        severity=cast("Severity", _str(data["severity"], "severity")),
        refs=_str_tuple(data["refs"], "refs"),
        message=_str(data["message"], "message"),
        rule_id=_str(data["rule_id"], "rule_id"),
        evidence_refs=_str_tuple(data["evidence_refs"], "evidence_refs"),
        next_checks=tuple(
            cast("NextCheck", item)
            for item in _str_tuple(data.get("next_checks", ()), "next_checks")
        ),
    )


def _read_json(path: Path) -> JsonRecord:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in {path}")
    return cast("JsonRecord", data)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return cast("Mapping[str, object]", value)


def _sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValueError(f"{field} must be a sequence")
    return value


def _str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _optional_str(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _str(value, field)


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _optional_bool(value: object, field: str) -> bool | None:
    if value is None:
        return None
    return _bool(value, field)


def _optional_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    return value


def _str_tuple(value: object, field: str) -> tuple[str, ...]:
    return tuple(_str(item, field) for item in _sequence(value, field))


def _pair_str_tuple(value: object, field: str) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for item in _sequence(value, field):
        pair = _sequence(item, field)
        if len(pair) != 2:
            raise ValueError(f"{field} items must have two values")
        pairs.append((_str(pair[0], field), _str(pair[1], field)))
    return tuple(pairs)


def _nullable_tuple(value: object, field: str) -> tuple[tuple[str, bool | None], ...]:
    pairs: list[tuple[str, bool | None]] = []
    for item in _sequence(value, field):
        pair = _sequence(item, field)
        if len(pair) != 2:
            raise ValueError(f"{field} items must have two values")
        pairs.append((_str(pair[0], field), _optional_bool(pair[1], field)))
    return tuple(pairs)


def _top_value(value: object) -> tuple[object, int]:
    pair = _sequence(value, "top_values item")
    if len(pair) != 2:
        raise ValueError("top_values items must have two values")
    return pair[0], _optional_int(pair[1], "top_values count") or 0
