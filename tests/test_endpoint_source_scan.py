"""Tests for non-Python endpoint extraction from source code.

Regression guard: discover_endpoints_ast() used to return [] for every
non-Python service, so on any Node/PHP project without an OpenAPI spec the
pipeline generated zero traffic and produced an empty analysis (found live
on docker/awesome-compose react-express-mysql: "Discovered 0 endpoints
across 2 service(s)" despite the backend defining real Express routes).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from boundary_analyzer.auto.models import ServiceInfo
from boundary_analyzer.auto.traffic import discover_endpoints_ast


def _svc(language: str) -> ServiceInfo:
    return ServiceInfo(name="svc", language=language, framework="", entry_points=[], deployment="docker-compose")


class SourceScanTestBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="epscan_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def _write(self, rel: str, content: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


class ExpressExtractionTest(SourceScanTestBase):
    def test_basic_app_routes(self):
        self._write("src/server.js", """
const app = require("express")();
app.get("/", (req, res) => res.send("hi"));
app.get("/healthz", (req, res) => res.send("ok"));
app.post('/api/users', createUser);
router.delete(`/api/users/:id`, deleteUser);
""")
        eps = {(e.method, e.path) for e in discover_endpoints_ast(_svc("node"), self.root)}
        self.assertIn(("GET", "/"), eps)
        self.assertIn(("GET", "/healthz"), eps)
        self.assertIn(("POST", "/api/users"), eps)
        # Express :id params normalized to {id}
        self.assertIn(("DELETE", "/api/users/{id}"), eps)

    def test_route_chain(self):
        self._write("routes.js", """
router.route('/api/books').get(listBooks).post(createBook);
""")
        eps = {(e.method, e.path) for e in discover_endpoints_ast(_svc("node"), self.root)}
        self.assertIn(("GET", "/api/books"), eps)
        self.assertIn(("POST", "/api/books"), eps)

    def test_node_modules_excluded(self):
        self._write("node_modules/somepkg/index.js", 'app.get("/should-not-appear", h);')
        self._write("src/app.js", 'app.get("/real", h);')
        eps = {e.path for e in discover_endpoints_ast(_svc("node"), self.root)}
        self.assertIn("/real", eps)
        self.assertNotIn("/should-not-appear", eps)

    def test_deduplicates(self):
        self._write("a.js", 'app.get("/x", h);')
        self._write("b.js", 'app.get("/x", h);')
        eps = discover_endpoints_ast(_svc("node"), self.root)
        self.assertEqual(len([e for e in eps if e.path == "/x"]), 1)


class NestJsExtractionTest(SourceScanTestBase):
    def test_controller_prefix_and_methods(self):
        self._write("src/users.controller.ts", """
@Controller('users')
export class UsersController {
  @Get()
  findAll() {}
  @Get(':id')
  findOne() {}
  @Post()
  create() {}
}
""")
        eps = {(e.method, e.path) for e in discover_endpoints_ast(_svc("node"), self.root)}
        self.assertIn(("GET", "/users"), eps)
        self.assertIn(("GET", "/users/{id}"), eps)
        self.assertIn(("POST", "/users"), eps)


class LaravelExtractionTest(SourceScanTestBase):
    def test_route_facade(self):
        self._write("routes/web.php", """<?php
Route::get('/orders', [OrderController::class, 'index']);
Route::post('/orders', [OrderController::class, 'store']);
Route::delete('/orders/{order}', [OrderController::class, 'destroy']);
""")
        eps = {(e.method, e.path) for e in discover_endpoints_ast(_svc("php"), self.root)}
        self.assertIn(("GET", "/orders"), eps)
        self.assertIn(("POST", "/orders"), eps)
        self.assertIn(("DELETE", "/orders/{order}"), eps)

    def test_vendor_excluded(self):
        self._write("vendor/pkg/routes.php", "<?php Route::get('/vendor-noise', h);")
        eps = {e.path for e in discover_endpoints_ast(_svc("php"), self.root)}
        self.assertNotIn("/vendor-noise", eps)


class PythonStillWorksTest(SourceScanTestBase):
    def test_flask_extraction_unchanged(self):
        self._write("app.py", """
from flask import Flask
app = Flask(__name__)

@app.route("/items", methods=["GET"])
def items():
    return []
""")
        eps = {(e.method, e.path) for e in discover_endpoints_ast(_svc("python"), self.root)}
        self.assertTrue(any(p == "/items" for _, p in eps))


if __name__ == "__main__":
    unittest.main()
