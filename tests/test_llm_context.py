from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class LllmContextTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, path: str, content: str) -> Path:
        full = self.tmpdir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return full

    def test_detect_fastapi_from_requirements(self):
        self._write("requirements.txt", "fastapi\nuvicorn\n")
        from boundary_analyzer.llm.context import _detect_framework_from_files
        result = _detect_framework_from_files(self.tmpdir)
        self.assertEqual(result, "fastapi")

    def test_detect_flask_from_requirements(self):
        self._write("requirements.txt", "flask\nsqlalchemy\n")
        from boundary_analyzer.llm.context import _detect_framework_from_files
        result = _detect_framework_from_files(self.tmpdir)
        self.assertEqual(result, "flask")

    def test_detect_django_from_manage_py(self):
        self._write("manage.py", "")
        from boundary_analyzer.llm.context import _detect_framework_from_files
        result = _detect_framework_from_files(self.tmpdir)
        self.assertEqual(result, "django")

    def test_detect_fastapi_from_imports(self):
        self._write("app/main.py", "from fastapi import FastAPI\n")
        from boundary_analyzer.llm.context import _detect_framework_from_files
        result = _detect_framework_from_files(self.tmpdir)
        self.assertEqual(result, "fastapi")

    def test_unknown_framework(self):
        self._write("app/main.py", "print('hello')\n")
        from boundary_analyzer.llm.context import _detect_framework_from_files
        result = _detect_framework_from_files(self.tmpdir)
        self.assertEqual(result, "unknown")

    def test_find_main_file(self):
        self._write("app/main.py", "print('main')\n")
        from boundary_analyzer.llm.context import _find_main_file
        result = _find_main_file(self.tmpdir)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "main.py")

    def test_find_main_file_root(self):
        self._write("main.py", "print('main')\n")
        from boundary_analyzer.llm.context import _find_main_file
        result = _find_main_file(self.tmpdir)
        self.assertIsNotNone(result)
        self.assertEqual(result.parent, self.tmpdir)

    def test_find_main_file_not_found(self):
        self._write("other.py", "print('hello')\n")
        from boundary_analyzer.llm.context import _find_main_file
        result = _find_main_file(self.tmpdir)
        self.assertIsNone(result)

    def test_detect_sqlalchemy_orm(self):
        self._write("requirements.txt", "sqlalchemy\nfastapi\n")
        from boundary_analyzer.llm.context import _detect_orm
        result = _detect_orm(self.tmpdir)
        self.assertEqual(result, "sqlalchemy")

    def test_detect_unknown_orm(self):
        self._write("requirements.txt", "fastapi\nuvicorn\n")
        from boundary_analyzer.llm.context import _detect_orm
        result = _detect_orm(self.tmpdir)
        self.assertEqual(result, "unknown")

    def test_detect_httpx_client(self):
        self._write("app/service.py", "import httpx\n")
        from boundary_analyzer.llm.context import _detect_http_client
        result = _detect_http_client(self.tmpdir)
        self.assertEqual(result, "httpx")

    def test_detect_requests_client(self):
        self._write("app/service.py", "import requests\n")
        from boundary_analyzer.llm.context import _detect_http_client
        result = _detect_http_client(self.tmpdir)
        self.assertEqual(result, "requests")

    def test_get_service_name(self):
        project = Path(tempfile.gettempdir()) / "my-service-name"
        from boundary_analyzer.llm.context import _get_service_name
        result = _get_service_name(project)
        self.assertEqual(result, "my-service-name")

    def test_scan_api_routes(self):
        self._write(
            "app/routes.py",
            "@router.get('/items')\n"
            "@router.post('/items')\n"
            "def not_a_route():\n"
            "    pass\n",
        )
        from boundary_analyzer.llm.context import _scan_api_routes
        routes = _scan_api_routes(self.tmpdir)
        self.assertEqual(len(routes), 2)
        self.assertIn(".get(", routes[0]["route"])
        self.assertIn(".post(", routes[1]["route"])

    def test_build_project_context(self):
        self._write("app/main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
        self._write("app/client.py", "import httpx\n")
        self._write("requirements.txt", "fastapi\nsqlalchemy\nhttpx\n")
        self._write("Dockerfile", "FROM python:3.11\n")

        from boundary_analyzer.llm.context import build_project_context
        ctx = build_project_context(self.tmpdir)

        self.assertEqual(ctx["service_name"], self.tmpdir.name.replace("_", "-"))
        self.assertEqual(ctx["framework"], "fastapi")
        self.assertEqual(ctx["orm"], "sqlalchemy")
        self.assertEqual(ctx["http_client"], "httpx")
        self.assertEqual(ctx["main_file"], os.path.join("app", "main.py"))
        self.assertTrue(ctx["has_dockerfile"])
        self.assertIsInstance(ctx["structure"], list)
        self.assertIsInstance(ctx["api_routes"], list)
        self.assertIn("FastAPI", ctx["main_content"])
        self.assertIn("fastapi", ctx["requirements_content"])

    def test_build_project_context_no_files(self):
        from boundary_analyzer.llm.context import build_project_context
        ctx = build_project_context(self.tmpdir)

        self.assertEqual(ctx["framework"], "unknown")
        self.assertIsNone(ctx["main_file"])
        self.assertIsNone(ctx["requirements_file"])
        self.assertFalse(ctx["has_dockerfile"])

    def test_format_context_for_prompt(self):
        ctx = {
            "service_name": "test-svc",
            "framework": "fastapi",
            "orm": "sqlalchemy",
            "http_client": "httpx",
            "main_file": "app/main.py",
            "requirements_file": "requirements.txt",
            "has_dockerfile": True,
            "structure": ["app/main.py", "requirements.txt"],
            "api_routes": [
                {"file": "app/routes.py", "line": "1", "route": "@router.get('/items')"}
            ],
            "main_content": "from fastapi import FastAPI\napp = FastAPI()\n",
            "requirements_content": "fastapi\n",
        }
        from boundary_analyzer.llm.context import format_context_for_prompt
        text = format_context_for_prompt(ctx)
        self.assertIn("test-svc", text)
        self.assertIn("fastapi", text)
        self.assertIn("sqlalchemy", text)
        self.assertIn("app/main.py", text)
        self.assertIn("@router.get('/items')", text)
        self.assertIn("from fastapi import FastAPI", text)
        self.assertIn("fastapi\n", text)

    def test_safe_read_missing_file(self):
        from boundary_analyzer.llm.context import _safe_read
        result = _safe_read(self.tmpdir / "nonexistent.py")
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
