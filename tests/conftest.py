"""
Shared pytest fixtures.

Adds the project root to sys.path so `import engine...` works when pytest is
run from anywhere, loads the real config.json, and exposes paths to the real
demo documents (tests that need them skip cleanly if the folder is absent).
"""
from __future__ import annotations

import os
import sys
import json
import copy

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Folder with the real demo documents (manual, target xlsx, mapping docx).
# Not part of the repo (real customer manuals) — point PARSEL_DEMO_DIR at your
# local copy to enable the `slow`/`requires_demo` tests; they skip cleanly
# otherwise. Falls back to a conventional per-user location.
DEMO_DIR = os.environ.get(
    "PARSEL_DEMO_DIR",
    os.path.join(os.path.expanduser("~"), "Downloads", "Spare parts documents"),
)
DEMO_FILES = {
    "manual_scanned": os.path.join(DEMO_DIR, "05 1.pdf"),
    "manual_digital": os.path.join(DEMO_DIR, "Book 1.pdf"),
    "target_xlsx": os.path.join(DEMO_DIR, "New Format (1) 2.xlsx"),
}

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


@pytest.fixture(scope="session")
def config() -> dict:
    with open(os.path.join(PROJECT_ROOT, "config.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def cfg(config) -> dict:
    """A fresh deep copy per test so mutations don't leak."""
    return copy.deepcopy(config)


@pytest.fixture(scope="session")
def golden_aliases() -> dict:
    path = os.path.join(DATA_DIR, "golden_aliases.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def paths(tmp_path) -> dict:
    return {
        "app_dir": str(tmp_path),
        "config_path": str(tmp_path / "config.json"),
        "wip_tracker": str(tmp_path / "WIP_Tracker.txt"),
        "models_dir": str(tmp_path / "models"),
    }


def demo_available(key: str) -> bool:
    return os.path.exists(DEMO_FILES.get(key, ""))


requires_demo = pytest.mark.skipif(
    not os.path.isdir(DEMO_DIR),
    reason="real demo documents folder not present",
)
