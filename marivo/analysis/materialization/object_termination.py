"""Process-local request proofs retained only while their journal obligations exist."""

from marivo.analysis.materialization.contracts import ResourceRecord

OBJECT_REQUEST_CAPABILITY = "s3_request@v1"
_TERMINATED: set[ResourceRecord] = set()


def prove_object_termination(resource: ResourceRecord) -> None:
    _TERMINATED.add(resource)


def object_request_is_terminal(resource: ResourceRecord) -> bool:
    return resource in _TERMINATED


def forget_object_termination(resources: tuple[ResourceRecord, ...]) -> None:
    """Called only after durable journal removal; rollback must retain proof."""
    _TERMINATED.difference_update(resources)
