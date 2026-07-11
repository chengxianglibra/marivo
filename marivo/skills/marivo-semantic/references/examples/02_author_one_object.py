"""Author one semantic object, verify it, then inspect readiness."""

from __future__ import annotations

from pathlib import Path

import marivo.datasource as md
import marivo.semantic as ms

ms.help("dimension_column")

declaration = (
    "import marivo.semantic as ms\n"
    "\n"
    "order_region = ms.dimension_column(\n"
    "    name='region',\n"
    "    entity=ms.ref('entity.sales.orders'),\n"
    "    column='region',\n"
    "    ai_context=ms.ai_context(\n"
    "        business_definition='Order reporting region.',\n"
    "    ),\n"
    ")\n"
)

semantic_file = Path("models/semantic/sales/order_region.py")
semantic_file.write_text(declaration)

warehouse = md.ref("datasource.warehouse")
inspection = md.inspect(warehouse, md.table("orders"))
snapshot = inspection.sample(
    scope=md.unpruned(max_rows=100, timeout_seconds=30),
    columns=("region",),
)

catalog = ms.load()
region = catalog.domains.get("sales").entities.get("orders").dimensions.get("region")
verification = catalog.verify_object(region)
verification.show()
if verification.status == "failed":
    raise SystemExit("verify failed for dimension.sales.orders.region")

catalog.preview(region, using=snapshot).show()
readiness = catalog.readiness(refs=[region])
readiness.show()
print("verified:", region.ref.id)
print("readiness:", readiness.status)
