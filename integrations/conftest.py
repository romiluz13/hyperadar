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

_test_db_name = os.environ.get("MONGODB_TEST_DB", "")
if not (_test_db_name.startswith("test_") or _test_db_name.endswith("_test")):
    raise pytest.UsageError(
        "Set MONGODB_TEST_DB to an explicit test-only database name "
        "(test_* or *_test); refusing to run against production."
    )
os.environ["MONGODB_DB"] = _test_db_name
os.environ["PORT_CLIENT_ID"] = "test-suite-no-network"
os.environ["PORT_CLIENT_SECRET"] = "test-suite-no-network"


def pytest_configure(config):
    config.option.asyncio_mode = "auto"


@pytest.fixture(scope="session")
def db():
    """Shared MongoDB fixture for all integration tests (session-scoped for
    compatibility with async tests)."""
    client = pymongo.MongoClient(os.environ["MONGODB_URI"])
    database = client[_test_db_name]
    reaction_key = [("postId", 1), ("userId", 1), ("type", 1)]
    for name, details in database.reactions.index_information().items():
        if (
            details.get("key") == reaction_key
            and details.get("unique")
            and details.get("partialFilterExpression") != {"type": "like"}
        ):
            database.reactions.drop_index(name)
    database.reactions.create_index(
        reaction_key,
        unique=True,
        partialFilterExpression={"type": "like"},
        name="one_like_per_user",
    )
    database.reactions.create_index(
        [("postId", 1), ("rankIdentity", 1), ("type", 1)],
        unique=True,
        partialFilterExpression={
            "type": "like",
            "rankIdentity": {"$type": "string"},
        },
        name="one_like_per_network",
    )
    database.reactions.create_index(
        "operationId",
        unique=True,
        partialFilterExpression={"operationId": {"$type": "string"}},
        name="one_reaction_per_operation",
    )
    database.embeddings_audit.create_index(
        "postId",
        unique=True,
        partialFilterExpression={"postId": {"$type": "string"}},
        name="one_embedding_audit_per_post",
    )
    database.posts.create_index(
        "publicationKey",
        unique=True,
        partialFilterExpression={"publicationKey": {"$type": "string"}},
        name="one_daily_agent_project_publication",
    )
    yield database
    client.close()


@pytest.fixture(autouse=True)
async def close_async_mongo_client():
    yield
    from _shared import mongo

    await mongo.close_client()
