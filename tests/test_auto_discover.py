import tempfile
import unittest
from pathlib import Path

import yaml

from boundary_analyzer.auto.discover import (
    _discover_compose_app_services,
    discover_project,
)
from boundary_analyzer.auto.errors import AnalysisError


class DiscoverTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="discover_test_"))

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, path: str, content: str) -> Path:
        full = self.tmpdir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return full

    def _write_compose(self, content: str):
        (self.tmpdir / "docker-compose.yml").write_text(content, encoding="utf-8")

    def test_discover_fastapi_project(self):
        self._write("requirements.txt", "fastapi==0.100")
        self._write("main.py", "from fastapi import FastAPI\napp = FastAPI()")
        project = discover_project(self.tmpdir)
        self.assertEqual(len(project.services), 1)
        self.assertEqual(project.language, "python")
        self.assertEqual(project.framework, "fastapi")
        self.assertEqual(project.services[0].name, "main")
        self.assertFalse(project.has_docker)

    def test_discover_flask_project(self):
        self._write("requirements.txt", "flask==2.0")
        self._write("app.py", "from flask import Flask\napp = Flask(__name__)")
        project = discover_project(self.tmpdir)
        self.assertEqual(len(project.services), 1)
        self.assertEqual(project.framework, "flask")

    def test_discover_django_project(self):
        self._write("requirements.txt", "django==4.2")
        self._write("manage.py", "#!/usr/bin/env python\nimport django")
        project = discover_project(self.tmpdir)
        self.assertEqual(len(project.services), 1)
        self.assertEqual(project.framework, "django")

    def test_discover_with_docker(self):
        self._write("requirements.txt", "fastapi==0.100")
        self._write("main.py", "from fastapi import FastAPI\napp = FastAPI()")
        self._write("Dockerfile", "FROM python:3.11")
        project = discover_project(self.tmpdir)
        self.assertTrue(project.has_docker)

    def test_discover_empty_dir_raises(self):
        with self.assertRaises(AnalysisError) as ctx:
            discover_project(self.tmpdir)
        self.assertEqual(ctx.exception.code.value, "discover.project.EMPTY")

    def test_discover_no_lang_raises(self):
        self._write("readme.md", "# hello")
        self._write("data.csv", "a,b,c")
        with self.assertRaises(AnalysisError) as ctx:
            discover_project(self.tmpdir)
        self.assertIn("NOT_FOUND", ctx.exception.code.value)

    def test_discover_nonexistent_dir_raises(self):
        with self.assertRaises(AnalysisError) as ctx:
            discover_project("/nonexistent/path/12345")
        self.assertEqual(ctx.exception.code.value, "discover.project.EMPTY")

    def test_discover_port_by_config(self):
        self._write("requirements.txt", "fastapi")
        self._write("main.py", "from fastapi import FastAPI\napp = FastAPI()")
        self._write(".env", "PORT=3000")
        project = discover_project(self.tmpdir)
        self.assertEqual(project.services[0].port, 3000)


class DockerComposeDiscoverTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="compose_discover_test_"))

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, path: str, content: str) -> Path:
        full = self.tmpdir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return full

    def test_discover_compose_app_services_finds_build_services(self):
        self._write(
            "docker-compose.yml",
            yaml.dump(
                {
                    "services": {
                        "web": {
                            "build": ".",
                            "ports": ["8000:5000"],
                        },
                        "db": {
                            "image": "postgres:15",
                        },
                    },
                }
            ),
        )
        result = _discover_compose_app_services(self.tmpdir)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "web")
        self.assertEqual(result[0][1], 8000)
        self.assertEqual(result[0][2], self.tmpdir.resolve())

    def test_discover_compose_app_services_skips_infra(self):
        self._write(
            "docker-compose.yml",
            yaml.dump(
                {
                    "services": {
                        "db": {"image": "postgres:15"},
                        "redis": {"image": "redis:7"},
                    },
                }
            ),
        )
        result = _discover_compose_app_services(self.tmpdir)
        self.assertEqual(len(result), 0)

    def test_discover_compose_app_services_no_file(self):
        result = _discover_compose_app_services(self.tmpdir)
        self.assertEqual(len(result), 0)

    def test_discover_compose_app_services_build_with_context_dict(self):
        self._write(
            "docker-compose.yml",
            yaml.dump(
                {
                    "services": {
                        "api": {
                            "build": {"context": "./app", "dockerfile": "Dockerfile"},
                            "ports": ["3000:3000"],
                        },
                    },
                }
            ),
        )
        (self.tmpdir / "app").mkdir()
        result = _discover_compose_app_services(self.tmpdir)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "api")
        self.assertEqual(result[0][1], 3000)
        self.assertEqual(result[0][2], (self.tmpdir / "app").resolve())

    def test_discover_project_with_compose(self):
        self._write(
            "docker-compose.yml",
            yaml.dump(
                {
                    "services": {
                        "scenario2": {
                            "build": "./app",
                            "ports": ["5102:5000"],
                        },
                        "db": {"image": "postgres:15"},
                    },
                }
            ),
        )
        (self.tmpdir / "app").mkdir()
        self._write("app/requirements.txt", "flask==2.0")
        self._write("app/app.py", "from flask import Flask\napp = Flask(__name__)")
        project = discover_project(self.tmpdir)
        self.assertTrue(project.has_docker)
        self.assertEqual(len(project.services), 1)
        service = project.services[0]
        self.assertEqual(service.name, "scenario2")
        self.assertEqual(service.deployment, "docker-compose")
        self.assertEqual(service.compose_service_name, "scenario2")
        self.assertEqual(service.port, 5102)
        self.assertEqual(service.language, "python")
        self.assertEqual(service.framework, "flask")

    def test_discover_project_with_compose_no_build_context(self):
        self._write(
            "docker-compose.yml",
            yaml.dump(
                {
                    "services": {
                        "web": {
                            "build": "./web",
                            "ports": ["8080:80"],
                        },
                    },
                }
            ),
        )
        (self.tmpdir / "web").mkdir()
        self._write("requirements.txt", "fastapi==0.100")
        self._write("main.py", "from fastapi import FastAPI\napp = FastAPI()")
        project = discover_project(self.tmpdir)
        self.assertEqual(len(project.services), 1)
        self.assertEqual(project.services[0].name, "web")
        self.assertEqual(project.services[0].framework, "fastapi")


class ExtractHostPortTest(unittest.TestCase):
    def test_short_format(self):
        from boundary_analyzer.auto.discover import _extract_host_port

        self.assertEqual(_extract_host_port(["5000:5000"]), 5000)

    def test_long_format_with_host_ip(self):
        from boundary_analyzer.auto.discover import _extract_host_port

        self.assertEqual(_extract_host_port(["127.0.0.1:5000:5000"]), 5000)

    def test_bare_integer(self):
        from boundary_analyzer.auto.discover import _extract_host_port

        self.assertEqual(_extract_host_port([8000]), 8000)

    def test_first_valid_port_returned(self):
        from boundary_analyzer.auto.discover import _extract_host_port

        self.assertEqual(_extract_host_port(["3000:3000", "4000:4000"]), 3000)

    def test_empty_list_returns_none(self):
        from boundary_analyzer.auto.discover import _extract_host_port

        self.assertIsNone(_extract_host_port([]))

    def test_no_valid_port_returns_none(self):
        from boundary_analyzer.auto.discover import _extract_host_port

        self.assertIsNone(_extract_host_port(["invalid", "also:bad"]))
