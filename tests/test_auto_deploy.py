import tempfile
import unittest
from pathlib import Path

import yaml

from boundary_analyzer.auto.deploy import (
    _build_compose_override,
    _find_compose_file,
    _get_python_original_cmd,
    _parse_dockerfile_cmd,
)
from boundary_analyzer.auto.models import EntryPoint, ProjectInfo, ServiceInfo


class ComposeOverrideTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="deploy_test_"))

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_project(self, services: list[ServiceInfo]) -> ProjectInfo:
        return ProjectInfo(
            services=services,
            root_dir=self.tmpdir,
            has_docker=True,
            language="python",
            framework="flask",
        )

    def test_build_override_adds_jaeger(self):
        project = self._make_project([])
        yaml_str = _build_compose_override(project)
        data = yaml.safe_load(yaml_str)
        self.assertIn("mba-jaeger", data["services"])
        self.assertIn("image", data["services"]["mba-jaeger"])
        self.assertEqual(
            data["services"]["mba-jaeger"]["image"],
            "jaegertracing/all-in-one:latest",
        )
        ports = data["services"]["mba-jaeger"]["ports"]
        self.assertIn("16686:16686", ports)
        self.assertIn("4318:4318", ports)
        self.assertNotIn("4317", " ".join(ports))

    def test_build_override_custom_container_name(self):
        project = self._make_project([])
        yaml_str = _build_compose_override(project, container_name="my-jaeger")
        data = yaml.safe_load(yaml_str)
        self.assertIn("my-jaeger", data["services"])
        self.assertNotIn("mba-jaeger", data["services"])

    def test_build_override_custom_ports(self):
        project = self._make_project([])
        yaml_str = _build_compose_override(project, jaeger_port=16687, otlp_port=4319)
        data = yaml.safe_load(yaml_str)
        ports = data["services"]["mba-jaeger"]["ports"]
        self.assertIn("16687:16686", ports)
        self.assertIn("4319:4318", ports)

    def test_build_override_adds_otel_env_for_compose_services(self):
        svc = ServiceInfo(
            name="myapp",
            language="python",
            framework="flask",
            entry_points=[EntryPoint(path=Path("app.py"), framework="flask")],
            deployment="docker-compose",
            compose_service_name="myapp",
            ports=[8000],
        )
        project = self._make_project([svc])
        yaml_str = _build_compose_override(project)
        data = yaml.safe_load(yaml_str)
        self.assertIn("myapp", data["services"])
        env = data["services"]["myapp"]["environment"]
        self.assertIn("OTEL_SERVICE_NAME=myapp", env)
        self.assertIn("OTEL_EXPORTER_OTLP_ENDPOINT=http://mba-jaeger:4317", env)
        self.assertIn("OTEL_PYTHON_CONFIGURATOR=opentelemetry-sdk-configurator", env)
        self.assertIn("depends_on", data["services"]["myapp"])
        self.assertIn("mba-jaeger", data["services"]["myapp"]["depends_on"])

    def test_build_override_skips_non_compose_services(self):
        svc = ServiceInfo(
            name="direct-app",
            language="python",
            framework="fastapi",
            entry_points=[EntryPoint(path=Path("main.py"), framework="fastapi")],
            deployment="direct",
            ports=[8000],
        )
        project = self._make_project([svc])
        yaml_str = _build_compose_override(project)
        data = yaml.safe_load(yaml_str)
        self.assertNotIn("direct-app", data["services"])

    def test_build_override_no_python_otel_for_java(self):
        svc = ServiceInfo(
            name="java-app",
            language="java",
            framework="spring",
            entry_points=[],
            deployment="docker-compose",
            compose_service_name="java-app",
            ports=[8080],
        )
        project = self._make_project([svc])
        yaml_str = _build_compose_override(project)
        data = yaml.safe_load(yaml_str)
        env = data["services"]["java-app"]["environment"]
        self.assertIn("OTEL_SERVICE_NAME=java-app", env)
        self.assertNotIn("OTEL_PYTHON_CONFIGURATOR", " ".join(env))

    def test_find_compose_file_yml(self):
        (self.tmpdir / "docker-compose.yml").write_text("", encoding="utf-8")
        result = _find_compose_file(self.tmpdir)
        self.assertEqual(result, self.tmpdir / "docker-compose.yml")

    def test_find_compose_file_yaml(self):
        (self.tmpdir / "docker-compose.yaml").write_text("", encoding="utf-8")
        result = _find_compose_file(self.tmpdir)
        self.assertEqual(result, self.tmpdir / "docker-compose.yaml")

    def test_find_compose_file_not_found(self):
        result = _find_compose_file(self.tmpdir)
        self.assertIsNone(result)


class ComposeOverrideJavaTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="deploy_java_test_"))

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_build_override_adds_node_otel(self):
        svc = ServiceInfo(
            name="node-app",
            language="node",
            framework="express",
            entry_points=[EntryPoint(path=Path("server.js"), framework="express")],
            deployment="docker-compose",
            compose_service_name="node-app",
            ports=[3000],
        )
        project = ProjectInfo(
            services=[svc],
            root_dir=self.tmpdir,
            has_docker=True,
            language="node",
            framework="express",
        )
        yaml_str = _build_compose_override(project)
        data = yaml.safe_load(yaml_str)
        self.assertIn("node-app", data["services"])
        env = data["services"]["node-app"]["environment"]
        self.assertIn("NODE_OPTIONS=--require @opentelemetry/auto-instrumentations-node/register", env)
        self.assertIn("OTEL_METRICS_EXPORTER=none", env)
        self.assertIn("OTEL_LOGS_EXPORTER=none", env)
        volumes = data["services"]["node-app"].get("volumes", [])
        self.assertEqual(len(volumes), 0)

    def test_build_override_adds_dotnet_otel(self):
        svc = ServiceInfo(
            name="dotnet-app",
            language="dotnet",
            framework="aspnet-core",
            entry_points=[EntryPoint(path=Path("WebApp.csproj"), framework="aspnet-core")],
            deployment="docker-compose",
            compose_service_name="dotnet-app",
            ports=[5000],
        )
        project = ProjectInfo(
            services=[svc],
            root_dir=self.tmpdir,
            has_docker=True,
            language="dotnet",
            framework="aspnet-core",
        )
        yaml_str = _build_compose_override(project)
        data = yaml.safe_load(yaml_str)
        self.assertIn("dotnet-app", data["services"])
        env = data["services"]["dotnet-app"]["environment"]
        self.assertIn("OTEL_DOTNET_AUTO_TRACES_EXPORTER=otlp", env)
        self.assertIn("OTEL_DOTNET_AUTO_METRICS_EXPORTER=none", env)
        self.assertIn("OTEL_DOTNET_AUTO_LOGS_EXPORTER=none", env)

    def test_build_override_adds_java_otel_and_volume(self):
        svc = ServiceInfo(
            name="java-app",
            language="java",
            framework="spring-boot",
            entry_points=[EntryPoint(path=Path("App.java"), framework="spring-boot")],
            deployment="docker-compose",
            compose_service_name="java-app",
            ports=[8080],
        )
        project = ProjectInfo(
            services=[svc],
            root_dir=self.tmpdir,
            has_docker=True,
            language="java",
            framework="spring-boot",
        )
        yaml_str = _build_compose_override(project)
        data = yaml.safe_load(yaml_str)
        self.assertIn("java-app", data["services"])
        env = data["services"]["java-app"]["environment"]
        self.assertIn("JAVA_TOOL_OPTIONS=-javaagent:/mba-agent/opentelemetry-javaagent.jar", env)
        self.assertIn("OTEL_METRICS_EXPORTER=none", env)
        self.assertIn("OTEL_LOGS_EXPORTER=none", env)
        volumes = data["services"]["java-app"].get("volumes", [])
        self.assertEqual(len(volumes), 1)
        self.assertIn("/mba-agent:ro", volumes[0])


class PythonInstrumentOverrideTest(unittest.TestCase):
    """Tests for Python auto-instrumentation in Docker Compose override."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="deploy_pyinst_"))

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- _parse_dockerfile_cmd ------------------------------------------------

    def test_parse_exec_form(self):
        self.assertEqual(
            _parse_dockerfile_cmd('["flask", "run"]'), ["flask", "run"]
        )

    def test_parse_exec_form_uvicorn(self):
        self.assertEqual(
            _parse_dockerfile_cmd('["uvicorn", "main:app", "--host", "0.0.0.0"]'),
            ["uvicorn", "main:app", "--host", "0.0.0.0"],
        )

    def test_parse_shell_form(self):
        self.assertEqual(
            _parse_dockerfile_cmd("flask run --host=0.0.0.0"),
            ["flask", "run", "--host=0.0.0.0"],
        )

    def test_parse_invalid_json(self):
        self.assertIsNone(_parse_dockerfile_cmd("[invalid"))

    def test_parse_empty(self):
        self.assertIsNone(_parse_dockerfile_cmd(""))
        self.assertIsNone(_parse_dockerfile_cmd("   "))

    def test_parse_non_list_json(self):
        # A quoted string is valid shell form — Docker runs: sh -c "just a string"
        self.assertEqual(
            _parse_dockerfile_cmd('"just a string"'), ["just a string"]
        )

    def test_parse_entrypoint_shell(self):
        self.assertEqual(
            _parse_dockerfile_cmd('["python", "app.py"]'), ["python", "app.py"]
        )

    # -- _get_python_original_cmd ---------------------------------------------

    def _create_compose(self, content: str):
        (self.tmpdir / "docker-compose.yml").write_text(content, encoding="utf-8")

    def _create_dockerfile(self, *path_parts: str, cmd: str | None = None):
        df_path = self.tmpdir.joinpath(*path_parts)
        df_path.parent.mkdir(parents=True, exist_ok=True)
        if cmd:
            df_path.write_text(f"FROM python:3.9-alpine\nCMD {cmd}\n", encoding="utf-8")

    def _make_service(self, name: str, compose_name: str, port: int = 5000) -> ServiceInfo:
        return ServiceInfo(
            name=name,
            language="python",
            framework="flask",
            entry_points=[EntryPoint(path=Path("app.py"), framework="flask")],
            deployment="docker-compose",
            compose_service_name=compose_name,
            ports=[port],
        )

    def test_get_cmd_from_dockerfile(self):
        self._create_compose("services:\n  web:\n    build: ./app\n    ports:\n      - 5000:5000\n")
        self._create_dockerfile("app", "Dockerfile", cmd='["flask", "run"]')
        svc = self._make_service("web", "web")
        result = _get_python_original_cmd(self.tmpdir, svc)
        self.assertEqual(result, ["flask", "run"])

    def test_get_cmd_no_compose_file(self):
        svc = self._make_service("web", "web")
        result = _get_python_original_cmd(self.tmpdir, svc)
        self.assertIsNone(result)

    def test_get_cmd_no_dockerfile(self):
        self._create_compose("services:\n  web:\n    build: ./app\n")
        svc = self._make_service("web", "web")
        result = _get_python_original_cmd(self.tmpdir, svc)
        self.assertIsNone(result)

    def test_get_cmd_entrypoint_and_cmd(self):
        self._create_compose("services:\n  web:\n    build: .\n")
        df = self.tmpdir / "Dockerfile"
        df.write_text(
            'FROM python:3.9-alpine\nENTRYPOINT ["python"]\nCMD ["app.py"]\n',
            encoding="utf-8",
        )
        svc = self._make_service("web", "web")
        result = _get_python_original_cmd(self.tmpdir, svc)
        self.assertEqual(result, ["python", "app.py"])

    def test_get_cmd_only_entrypoint(self):
        self._create_compose("services:\n  web:\n    build: .\n")
        df = self.tmpdir / "Dockerfile"
        df.write_text(
            'FROM python:3.9-alpine\nENTRYPOINT ["python", "app.py"]\n',
            encoding="utf-8",
        )
        svc = self._make_service("web", "web")
        result = _get_python_original_cmd(self.tmpdir, svc)
        self.assertEqual(result, ["python", "app.py"])

    # -- _build_compose_override with Dockerfile ------------------------------

    def test_override_python_service_gets_instrument_command(self):
        self._create_compose("services:\n  web:\n    build: ./app\n    ports:\n      - 5000:5000\n")
        self._create_dockerfile("app", "Dockerfile", cmd='["flask", "run"]')
        svc = self._make_service("web", "web")
        project = ProjectInfo(
            services=[svc],
            root_dir=self.tmpdir,
            has_docker=True,
            language="python",
            framework="flask",
        )
        yaml_str = _build_compose_override(project)
        data = yaml.safe_load(yaml_str)

        self.assertIn("web", data["services"])
        svc_cfg = data["services"]["web"]
        env = svc_cfg["environment"]
        self.assertIn("OTEL_SERVICE_NAME=web", env)
        self.assertIn("OTEL_EXPORTER_OTLP_ENDPOINT=http://mba-jaeger:4318", env)
        self.assertIn("OTEL_TRACES_EXPORTER=otlp_proto_http", env)

        self.assertIn("entrypoint", svc_cfg)
        self.assertEqual(svc_cfg["entrypoint"], ["/bin/sh", "-c"])
        self.assertIn("command", svc_cfg)
        command = svc_cfg["command"]
        self.assertIn("pip install --quiet opentelemetry-api", command)
        self.assertIn("opentelemetry-sdk", command)
        self.assertIn("opentelemetry-instrumentation", command)
        self.assertIn("opentelemetry-exporter-otlp-proto-http", command)
        self.assertIn("opentelemetry-instrumentation-flask", command)
        self.assertIn("opentelemetry-instrument flask run", command)
        self.assertIn("exec", command)

    def test_override_python_no_compose_no_command(self):
        svc = self._make_service("web", "web")
        project = ProjectInfo(
            services=[svc],
            root_dir=self.tmpdir,
            has_docker=True,
            language="python",
            framework="flask",
        )
        yaml_str = _build_compose_override(project)
        data = yaml.safe_load(yaml_str)

        self.assertIn("web", data["services"])
        svc_cfg = data["services"]["web"]
        self.assertIn("environment", svc_cfg)
        self.assertNotIn("entrypoint", svc_cfg)
        self.assertNotIn("command", svc_cfg)
