"""Pytest configuration for all HypeRadar integration tests.

Sets asyncio_mode=auto so async test methods work without @pytest.mark.asyncio.
Adds the integrations/ dir to sys.path so `from _shared import ...` works.
Provides a shared `db` fixture.
"""
import os
import sys
from pathlib import Path

import pymongo
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()


def pytest_configure(config):
    config.option.asyncio_mode = "auto"


@pytest.fixture()
def db():
    """Shared MongoDB fixture for all integration tests."""
    client = pymongo.MongoClient(os.environ["MONGODB_URI"])
    return client[os.environ.get("MONGODB_DB", "hyperadar")]
