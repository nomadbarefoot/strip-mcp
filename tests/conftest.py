"""Shared fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

MOCK_SERVER = str(Path(__file__).parent / "mock_mcp_server.py")
PYTHON = sys.executable


@pytest.fixture
def mock_server_cmd() -> list[str]:
    return [PYTHON, MOCK_SERVER]


@pytest.fixture
def mock_server_cmd_10() -> list[str]:
    return [PYTHON, MOCK_SERVER, "--tools", "10"]
