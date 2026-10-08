"""Pure receipt/resource ownership checks shared by publication and recovery."""

from marivo.analysis.materialization import contracts as codec
from marivo.analysis.materialization.contracts import (
    ResourceRecord,
    StorageReceipt,
)


def validate_receipt_owner(receipt: StorageReceipt, local_prefix: str) -> None:
    path = receipt.project_relative_path
    if path != local_prefix and not path.startswith(local_prefix + "/"):
        raise codec.invalid("receipt is outside its owning Artifact directory")


def owns_resource(receipt: StorageReceipt, resource: ResourceRecord) -> bool:
    """Match exact ownership; malformed persisted locators raise IntegrityError."""
    path = receipt.project_relative_path
    return (
        resource.cleanup_capability_id == "local_owned_path@v1"
        and resource.resource_kind == "local_storage_staging"
        and (path == resource.safe_locator or path.startswith(resource.safe_locator + "/"))
    )
