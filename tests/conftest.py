"""Shared pytest fixtures.

Project-wide fixtures live here. Phase-specific fixtures should live alongside their
tests (e.g. ``tests/foundation/conftest.py``) once they exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Absolute path to the repository root."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def config_dir(project_root: Path) -> Path:
    """Absolute path to the ``config/`` directory."""
    return project_root / "config"


@pytest.fixture(scope="session")
def conventions_yaml(config_dir: Path) -> Path:
    """Absolute path to the canonical conventions.yaml."""
    return config_dir / "conventions.yaml"
