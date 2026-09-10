"""Version-pinned native SDK stubs shared by Candidate object Runtime tests."""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from marivo.analysis.materialization import object_storage
from marivo.analysis.materialization.targets import S3Access

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


@dataclass(frozen=True, slots=True)
class CandidateObjects:
    stored: dict[str, tuple[str, bytes, dict[str, object]]]
    reads: list[tuple[str, str]]


def stub_candidate_objects(monkeypatch: pytest.MonkeyPatch, access: S3Access) -> CandidateObjects:
    """Install exact-version object reads and reversible native SDK writes."""
    base = object_storage.client
    stored: dict[str, tuple[str, bytes, dict[str, object]]] = {}
    reads: list[tuple[str, str]] = []

    @contextmanager
    def selected(binding: S3Access) -> Iterator[S3Client]:
        assert binding == access
        with base(binding) as client, Stubber(client) as stub:
            transport_method = "_make_api_call"
            original: Callable[[str, dict[str, object]], object] = getattr(client, transport_method)

            def request(operation: str, params: dict[str, object]) -> object:
                key = str(params.get("Key", ""))
                response: dict[str, object]
                if operation == "GetBucketVersioning":
                    response = {"Status": "Enabled"}
                elif operation == "PutObject":
                    body = params["Body"]
                    data = (
                        body
                        if isinstance(body, bytes)
                        else body.read()
                        if isinstance(body, io.BufferedIOBase)
                        else None
                    )
                    assert (
                        isinstance(data, bytes)
                        and len(data) == params["ContentLength"]
                        and key not in stored
                    )
                    metadata = params["Metadata"]
                    assert isinstance(metadata, dict)
                    version = f"version-{len(stored) + 1}"
                    stored[key] = (version, data, metadata)
                    response = {"VersionId": version}
                elif operation in ("HeadObject", "GetObject"):
                    version, data, metadata = stored[key]
                    assert params["VersionId"] == version
                    reads.append((key, version))
                    if operation == "HeadObject":
                        response = {
                            "VersionId": version,
                            "ContentLength": len(data),
                            "Metadata": metadata,
                        }
                    else:
                        start, end = (
                            int(v) for v in str(params["Range"]).removeprefix("bytes=").split("-")
                        )
                        chunk = data[start : end + 1]
                        response = {
                            "VersionId": version,
                            "Body": StreamingBody(io.BytesIO(chunk), len(chunk)),
                        }
                elif operation == "ListObjectVersions":
                    prefix = str(params["Prefix"])
                    response = {
                        "IsTruncated": False,
                        "Versions": [
                            {"Key": key, "VersionId": v[0]}
                            for key, v in stored.items()
                            if key.startswith(prefix)
                        ],
                    }
                elif operation == "DeleteObject":
                    assert params["VersionId"] == stored[key][0]
                    del stored[key]
                    response = {}
                else:
                    raise AssertionError(operation)
                names = {
                    "GetBucketVersioning": "get_bucket_versioning",
                    "PutObject": "put_object",
                    "HeadObject": "head_object",
                    "GetObject": "get_object",
                    "ListObjectVersions": "list_object_versions",
                    "DeleteObject": "delete_object",
                }
                stub.add_response(names[operation], response, params)
                return original(operation, params)

            monkeypatch.setattr(client, "_make_api_call", request)
            yield client
            stub.assert_no_pending_responses()

    monkeypatch.setattr(object_storage, "client", selected)
    return CandidateObjects(stored, reads)
