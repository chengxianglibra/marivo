from app.registry.datasource_registry import DatasourceRegistry
from app.registry.factories import build_catalog_adapter


class DatasourceService(DatasourceRegistry):
    """Thin compatibility facade over DatasourceRegistry."""
