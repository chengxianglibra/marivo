"""Assemble the datasource Help manifest from owner specifications."""

from types import MappingProxyType

from marivo.datasource import errors
from marivo.datasource._help_specs.connection_specs import SPECS as CONNECTIONS
from marivo.datasource._help_specs.inspection_specs import SPECS as INSPECTIONS
from marivo.datasource._help_specs.model import DatasourceManifest
from marivo.datasource._help_specs.navigation import GROUPS, NAVIGATION_ROUTES
from marivo.datasource._help_specs.source_specs import SPECS as SOURCES
from marivo.datasource._help_specs.type_specs import TYPE_CONTRACTS as TYPE_CONTRACTS

REGISTRY = DatasourceManifest(
    "datasource",
    (*CONNECTIONS, *SOURCES, *INSPECTIONS),
    GROUPS,
    MappingProxyType(NAVIGATION_ROUTES),
)
ERROR_TYPES = MappingProxyType(
    {
        name: value
        for name, value in vars(errors).items()
        if isinstance(value, type)
        and issubclass(value, errors.DatasourceError)
        and value is not errors.DatasourceError
    }
)
