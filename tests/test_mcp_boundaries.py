from __future__ import annotations

import unittest

from app.mcp.models import ResponseFormat as NewResponseFormat
from app.mcp.renderers import render_catalog_markdown as new_render_catalog_markdown
from app.mcp.server import mcp as new_mcp
from app.mcp_server import ResponseFormat, mcp, render_catalog_markdown


class MCPBoundaryTests(unittest.TestCase):
    def test_legacy_facade_reexports_new_mcp_public_api(self) -> None:
        self.assertIs(ResponseFormat, NewResponseFormat)
        self.assertIs(render_catalog_markdown, new_render_catalog_markdown)
        self.assertIs(mcp, new_mcp)
