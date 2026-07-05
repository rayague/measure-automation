"""Tests for the TeaStore traffic generator's real-ID discovery.

Regression guard for a bug found by auditing TeaStore's actual source
(DescartesResearch/TeaStore CategoryServlet.java / ProductServlet.java):
the WebUI maps ``/category`` and ``/product`` as exact paths and reads the
entity ID from a query-string parameter (``?category=<id>``, ``?id=<id>``),
never from a path segment. The old traffic generator requested
``/category/1``, ``/product/1``, etc., which 404s — the WebUI never actually
queries persistence-service for catalog data, so almost no business-endpoint
traces were ever produced by ``mba teastore``.

These tests mock HTTP responses (no Docker/live TeaStore required) to verify
the fixed generator builds correct, discoverable, query-string-based URLs.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from boundary_analyzer.auto.teastore_runner import (
    _build_traffic_paths,
    _discover_category_ids,
    _discover_product_ids,
)

_INDEX_HTML = """
<html><body>
<nav>
  <a href="category?category=2&page=1">Tea</a>
  <a href="category?category=3&page=1">Coffee</a>
  <a href="category?category=2&page=1">Tea (duplicate link)</a>
</nav>
</body></html>
"""

_CATEGORY_HTML = """
<html><body>
<div class="product"><a href="product?id=7">Green Tea</a></div>
<div class="product"><a href="product?id=8">Black Tea</a></div>
</body></html>
"""


def _fake_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status.return_value = None
    return resp


class DiscoverCategoryIdsTest(unittest.TestCase):
    @patch("boundary_analyzer.auto.teastore_runner.requests.get")
    def test_parses_category_ids_from_index_page(self, mock_get):
        mock_get.return_value = _fake_response(_INDEX_HTML)
        ids = _discover_category_ids("http://localhost:8080")
        self.assertEqual(ids, [2, 3])  # sorted, deduplicated

    @patch("boundary_analyzer.auto.teastore_runner.requests.get")
    def test_no_category_links_returns_empty_list(self, mock_get):
        mock_get.return_value = _fake_response("<html><body>empty catalog</body></html>")
        self.assertEqual(_discover_category_ids("http://localhost:8080"), [])

    @patch("boundary_analyzer.auto.teastore_runner.requests.get")
    def test_network_failure_returns_empty_list_not_raise(self, mock_get):
        import requests

        mock_get.side_effect = requests.ConnectionError("boom")
        self.assertEqual(_discover_category_ids("http://localhost:8080"), [])


class DiscoverProductIdsTest(unittest.TestCase):
    @patch("boundary_analyzer.auto.teastore_runner.requests.get")
    def test_parses_product_ids_from_category_page(self, mock_get):
        mock_get.return_value = _fake_response(_CATEGORY_HTML)
        ids = _discover_product_ids("http://localhost:8080", category_id=2)
        self.assertEqual(ids, [7, 8])

    @patch("boundary_analyzer.auto.teastore_runner.requests.get")
    def test_uses_query_string_params_not_path_segment(self, mock_get):
        mock_get.return_value = _fake_response(_CATEGORY_HTML)
        _discover_product_ids("http://localhost:8080", category_id=2)
        called_url = mock_get.call_args.args[0]
        called_params = mock_get.call_args.kwargs.get("params", {})
        self.assertTrue(called_url.endswith("/category"))  # exact path, no /2 suffix
        self.assertEqual(called_params, {"category": 2, "page": 1})


class BuildTrafficPathsTest(unittest.TestCase):
    @patch("boundary_analyzer.auto.teastore_runner._discover_product_ids")
    @patch("boundary_analyzer.auto.teastore_runner._discover_category_ids")
    def test_builds_query_string_paths_with_real_ids(self, mock_cats, mock_prods):
        mock_cats.return_value = [2, 3]
        mock_prods.return_value = [7, 8]

        paths = _build_traffic_paths("http://localhost:8080")

        self.assertIn("/tools.descartes.teastore.webui/category?category=2&page=1", paths)
        self.assertIn("/tools.descartes.teastore.webui/category?category=3&page=1", paths)
        self.assertIn("/tools.descartes.teastore.webui/product?id=7", paths)
        # The old, broken shape must never be produced again.
        for p in paths:
            self.assertNotRegex(p, r"/category/\d+$")
            self.assertNotRegex(p, r"/product/\d+$")

    @patch("boundary_analyzer.auto.teastore_runner._discover_product_ids")
    @patch("boundary_analyzer.auto.teastore_runner._discover_category_ids")
    def test_falls_back_to_static_pages_when_catalog_empty(self, mock_cats, mock_prods):
        mock_cats.return_value = []
        mock_prods.return_value = []

        paths = _build_traffic_paths("http://localhost:8080")

        self.assertIn("/tools.descartes.teastore.webui/", paths)
        self.assertIn("/tools.descartes.teastore.webui/login", paths)
        self.assertIn("/tools.descartes.teastore.webui/cart", paths)
        self.assertFalse(any("category?" in p for p in paths))
        self.assertFalse(any("product?" in p for p in paths))


if __name__ == "__main__":
    unittest.main()
