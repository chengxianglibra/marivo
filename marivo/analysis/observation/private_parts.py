"""Authority inventory for state that must remain inside its source engine."""

from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.observation.distinct_contracts import membership_part_authorities
from marivo.analysis.observation.distribution_contracts import distribution_part_authorities
from marivo.analysis.observation.fold_contracts import MetricFoldAuthorityV1


def source_private_part_authorities(
    row: DatasetRowContract,
) -> tuple[tuple[str, MetricFoldAuthorityV1], ...]:
    return (*membership_part_authorities(row), *distribution_part_authorities(row))
