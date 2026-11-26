"""Pytest configuration for all HypeRadar integration tests.

Sets asyncio_mode=auto so async test methods work without @pytest.mark.asyncio.
Adds the integrations/ dir to sys.path so `from _shared import ...` works.
Provides a shared `db` fixture.

Each session derives its own database from the MONGODB_TEST_DB prefix
(``hyperadar_test`` -> ``hyperadar_<8 hex>_test``) and drops it at the end,
so concurrent sessions sharing one prefix never see or drop each other's data.
"""

import os
import sys
import uuid
from pathlib import Path

import pymongo
import pytest
from dotenv import load_dotenv
from pymongo.errors import PyMongoError

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()


def _is_test_db_name(name: str) -> bool:
    return name.startswith("test_") or name.endswith("_test")


def _session_db_name(prefix: str) -> str:
    token = uuid.uuid4().hex[:8]
    if prefix.endswith("_test"):
        return f"{prefix[: -len('_test')]}_{token}_test"
    return f"{prefix}_{token}"


_test_db_prefix = os.environ.get("MONGODB_TEST_DB", "")
if not _is_test_db_name(_test_db_prefix):
    raise pytest.UsageError(
        "Set MONGODB_TEST_DB to an explicit test-only database name "
        "(test_* or *_test); refusing to run against production."
    )
_test_db_name = _session_db_name(_test_db_prefix)
os.environ["MONGODB_DB"] = _test_db_name
os.environ["PORT_CLIENT_ID"] = "test-suite-no-network"
os.environ["PORT_CLIENT_SECRET"] = "test-suite-no-network"


def pytest_configure(config):
    config.option.asyncio_mode = "auto"


def pytest_sessionfinish(session, exitstatus):
    client = pymongo.MongoClient(
        os.environ["MONGODB_URI"], serverSelectionTimeoutMS=5000
    )
    try:
        client.drop_database(_test_db_name)
    except PyMongoError as error:
        print(f"\nWARNING: could not drop {_test_db_name}: {error}", file=sys.stderr)
    finally:
        client.close()


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
