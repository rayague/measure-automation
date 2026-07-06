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
    _docker_cleanup_teastore,
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

    @patch("boundary_analyzer.auto.teastore_runner.requests.get")
    def test_tolerates_jsessionid_rewritten_links(self, mock_get):
        # TeaStore's <c:url> JSP tag appends ;jsessionid=<hex> to every link
        # when the client has no session cookie yet (first request always).
        # Confirmed live: without tolerating this, product discovery found 0
        # products on a real deployment.
        html = """
        <a href="category;jsessionid=8A3F00D9C2?category=2&page=1">Tea</a>
        <a href="product;jsessionid=8A3F00D9C2?id=42">Green Tea</a>
        """
        mock_get.return_value = _fake_response(html)
        self.assertEqual(_discover_category_ids("http://localhost:8080"), [2])
        self.assertEqual(_discover_product_ids("http://localhost:8080", 2), [42])

    @patch("boundary_analyzer.auto.teastore_runner.requests.get")
    def test_discovery_urls_include_webui_context_path(self, mock_get):
        # Confirmed live: requesting /category at the Tomcat root (outside the
        # /tools.descartes.teastore.webui context) 404s, so product discovery
        # silently returned []. Both discovery URLs must carry the context path.
        mock_get.return_value = _fake_response("<html></html>")
        _discover_category_ids("http://localhost:8080")
        _discover_product_ids("http://localhost:8080", 2)
        for call in mock_get.call_args_list:
            self.assertIn("/tools.descartes.teastore.webui", call.args[0])


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


class DockerCleanupTest(unittest.TestCase):
    """Regression guard for a cleanup bug found live: leftover containers from
    a failed run were never removed by the next run's pre-flight cleanup,
    because (a) `docker compose down` was targeting the wrong compose file
    (and therefore the wrong Compose project name) and (b) `docker container
    prune --filter name=...` is not a valid filter for that command at all
    (the daemon rejects it with "invalid filter 'name'"), so it silently did
    nothing. Together this let a stale, port-bound container survive across
    runs and break every subsequent `mba teastore` invocation."""

    @patch("boundary_analyzer.auto.teastore_runner.subprocess.run")
    def test_uses_patched_compose_file_when_present(self, mock_run):
        from boundary_analyzer.auto import teastore_runner as mod

        mock_run.return_value = MagicMock(stdout="", returncode=0)
        mod._PATCHED_COMPOSE.parent.mkdir(parents=True, exist_ok=True)
        mod._PATCHED_COMPOSE.write_text("placeholder", encoding="utf-8")
        self.addCleanup(lambda: mod._PATCHED_COMPOSE.unlink(missing_ok=True))

        _docker_cleanup_teastore()

        down_call = mock_run.call_args_list[0]
        cmd = down_call.args[0]
        self.assertIn(str(mod._PATCHED_COMPOSE), cmd)
        self.assertNotIn(str(mod._COMPOSE_SRC), cmd)

    @patch("boundary_analyzer.auto.teastore_runner.subprocess.run")
    def test_never_passes_invalid_name_filter_to_prune(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        _docker_cleanup_teastore()

        for call in mock_run.call_args_list:
            cmd = call.args[0]
            self.assertNotIn("prune", cmd, f"docker container/network prune does not support --filter name=...: {cmd}")

    @patch("boundary_analyzer.auto.teastore_runner.subprocess.run")
    def test_lists_then_force_removes_containers_by_name(self, mock_run):
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["docker", "ps"]:
                return MagicMock(stdout="abc123\ndef456\n", returncode=0)
            return MagicMock(stdout="", returncode=0)

        mock_run.side_effect = fake_run
        _docker_cleanup_teastore()

        rm_calls = [c.args[0] for c in mock_run.call_args_list if c.args[0][:2] == ["docker", "rm"]]
        self.assertEqual(len(rm_calls), 1)
        self.assertIn("abc123", rm_calls[0])
        self.assertIn("def456", rm_calls[0])


if __name__ == "__main__":
    unittest.main()
