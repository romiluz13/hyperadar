"""Pytest configuration for all HypeRadar integration tests.

Sets asyncio_mode=auto so async test methods work without @pytest.mark.asyncio.
Adds the integrations/ dir to sys.path so `from _shared import ...` works.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def pytest_configure(config):
    config.option.asyncio_mode = "auto"
