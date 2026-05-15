"""Generated contract models - do not edit manually.

Regenerate with: python scripts/generate_contract_models.py
"""

from pydantic import RootModel

from . import aoi as aoi
from . import osi as osi
from .aoi import TimeScope as TimeScope


class AIContext(RootModel[str | osi.AIContext1]):
    """Root model accepting either a plain string or structured AI context object."""

    root: str | osi.AIContext1


AIContextObject = osi.AIContext1
Field = osi.FieldModel
CustomExtension = osi.CustomExtension
Dataset = osi.Dataset
DialectExpression = osi.DialectExpression
Dimension = osi.Dimension
Expression = osi.Expression
Metric = osi.Metric
OSIDocument = osi.OsiCoreMetadataSpecificationWithMarivoVendorExtensions
Relationship = osi.Relationship
SemanticModel = osi.SemanticModel

OSI_MARIVO_SPEC_VERSION = "0.1.1"
AOI_SPEC_VERSION = "0.1.0"
