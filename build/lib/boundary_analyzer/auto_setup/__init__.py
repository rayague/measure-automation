from __future__ import annotations

from boundary_analyzer.auto_setup._detect import detect_framework
from boundary_analyzer.auto_setup._install import install_packages
from boundary_analyzer.auto_setup.setup_instrumentation import main as setup_main

__all__ = [
    "setup_main",
    "detect_framework",
    "install_packages",
]
