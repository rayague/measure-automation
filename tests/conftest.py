from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_project():
    """Create a temporary project directory and clean it up after the test."""
    tmpdir = Path(tempfile.mkdtemp(prefix="test_project_"))
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean_env():
    """Restore environment variables after each test (in case of leaks)."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)
