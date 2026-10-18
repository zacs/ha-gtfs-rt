"""Consistency checks between the integration metadata files."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "gtfs_rt"


def test_requirements_match_test_pins() -> None:
    """Tests must run against the exact library version users install."""
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    test_pins = {
        line.strip()
        for line in (ROOT / "requirements_test.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }

    for requirement in manifest["requirements"]:
        assert requirement in test_pins


def test_versions_match() -> None:
    """The manifest and pyproject versions move together."""
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    pyproject = (ROOT / "pyproject.toml").read_text()

    assert f'version = "{manifest["version"]}"' in pyproject
