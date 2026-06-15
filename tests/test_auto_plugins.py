import unittest

from boundary_analyzer.auto.plugins import (
    _ensure_loaded,
    list_supported_languages,
    register,
)
from boundary_analyzer.auto.plugins.java import JavaPlugin
from boundary_analyzer.auto.plugins.python import PythonPlugin


class PluginRegistryTest(unittest.TestCase):
    def test_all_plugins_registered(self):
        _ensure_loaded()
        languages = list_supported_languages()
        self.assertIn("python", languages)
        self.assertIn("java", languages)
        self.assertIn("node", languages)
        self.assertIn("php", languages)
        self.assertIn("dotnet", languages)

    def test_register_plugin(self):
        before = len(list_supported_languages())
        dummy = PythonPlugin()
        register(dummy)
        after = len(list_supported_languages())
        self.assertEqual(after, before + 1)

    def test_php_plugin_is_registered(self):
        _ensure_loaded()
        self.assertIn("php", list_supported_languages())

    def test_dotnet_plugin_is_registered(self):
        _ensure_loaded()
        self.assertIn("dotnet", list_supported_languages())

    def test_python_plugin_is_python(self):
        _ensure_loaded()
        p = PythonPlugin()
        self.assertEqual(p.name, "python")
        self.assertTrue(p.has_openapi())

    def test_java_plugin_is_java(self):
        _ensure_loaded()
        j = JavaPlugin()
        self.assertEqual(j.name, "java")
        self.assertTrue(j.has_openapi())

    def test_node_plugin_is_registered(self):
        _ensure_loaded()
        self.assertIn("node", list_supported_languages())
