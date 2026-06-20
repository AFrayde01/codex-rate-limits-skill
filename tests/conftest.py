from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture
def module():
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "codex-rate-limit-reset" / "scripts" / "read_rate_limits.py"
    spec = importlib.util.spec_from_file_location("read_rate_limits", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {script_path}")
    loaded_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded_module
    spec.loader.exec_module(loaded_module)
    return loaded_module
