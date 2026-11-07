"""Shared pytest fixtures.

Puts the project root on ``sys.path`` so ``import app`` works no matter where
pytest is invoked from, and points the app at a throwaway data directory.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Isolate every test run from the developer's real data/output directories.
_TMP = Path(tempfile.mkdtemp(prefix="fvm-tests-"))
os.environ.setdefault("FVM_DATA_DIR", str(_TMP / "data"))
os.environ.setdefault("FVM_OUTPUT_DIR", str(_TMP / "output"))
# Force the offline scriptwriter so tests never depend on the network.
os.environ.pop("OPENAI_API_KEY", None)


@pytest.fixture(scope="session")
def tmp_root() -> Path:
    return _TMP


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d
