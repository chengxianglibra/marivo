"""Pure receipt/resource ownership checks shared by publication and recovery."""

from marivo.analysis.materialization import contracts as codec
from marivo.analysis.materialization.contracts import (
    LocalReceipt,
    ObjectReceipt,
    ResourceRecord,
    StorageReceipt,
)


def object_artifact_prefix(session_ref: str, artifact_ref: str) -> str:
    return f"marivo/v3/{session_ref}/{artifact_ref}/"


def validate_receipt_owner(receipt: StorageReceipt, local_prefix: str, object_prefix: str) -> None:
    if isinstance(receipt, ObjectReceipt):
        if not receipt.immutable_prefix_or_manifest_ref.startswith(
            object_prefix
        ) or not receipt.immutable_prefix_or_manifest_ref.endswith("/manifest.json"):
            raise codec.invalid("object receipt is outside its owning Artifact prefix")
    else:
        path = (
            receipt.project_relative_path
            if isinstance(receipt, LocalReceipt)
            else receipt.qualified_relation_ref
        )
        if path != local_prefix and not path.startswith(local_prefix + "/"):
            raise codec.invalid("receipt is outside its owning Artifact directory")


def owns_resource(receipt: StorageReceipt, resource: ResourceRecord) -> bool:
    """Match exact ownership; malformed persisted locators raise IntegrityError."""
    if isinstance(receipt, ObjectReceipt):
        if (
            resource.resource_kind != "object_storage_staging"
            or resource.cleanup_capability_id != "s3_versioned_key@v1"
        ):
            return False
        obj = codec._obj(codec.parse_json(resource.safe_locator), "object_store_ref key")
        manifest = receipt.immutable_prefix_or_manifest_ref
        return (
            obj["object_store_ref"] == receipt.object_store_ref
            and resource.execution_domain_id == receipt.object_store_ref
            and obj["key"] in (manifest, manifest.removesuffix("manifest.json") + "data.parquet")
        )
    path = (
        receipt.project_relative_path
        if isinstance(receipt, LocalReceipt)
        else receipt.qualified_relation_ref
    )
    return (
        resource.cleanup_capability_id == "local_owned_path@v1"
        and resource.resource_kind
        == (
            "local_storage_staging"
            if isinstance(receipt, LocalReceipt)
            else "engine_storage_staging"
        )
        and (path == resource.safe_locator or path.startswith(resource.safe_locator + "/"))
    )
