"""Author one semantic object, verify it, then inspect readiness."""

from __future__ import annotations

from pathlib import Path

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

ref = ms.ref("dimension.sales.orders.region")
verification = ms.verify_object(ref)
verification.show()
if verification.status == "failed":
    raise SystemExit("verify failed for dimension.sales.orders.region")

readiness = ms.readiness(refs=(ref,))
readiness.show()
print("verified:", ref.id)
print("readiness:", readiness.status)
