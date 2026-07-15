import unittest
from datetime import datetime, timezone

from pymongo.errors import OperationFailure

from setup_mongodb import (
    backfill_publication_identities,
    ensure_index,
    ensure_reaction_indexes,
    reconcile_reaction_counts,
)


class FakeCollection:
    def __init__(self, indexes=None, fail_index=None):
        self.name = "reactions"
        self.indexes = dict(indexes or {})
        self.fail_index = fail_index
        self.dropped = []

    def index_information(self):
        return dict(self.indexes)

    def create_index(self, keys, **kwargs):
        name = kwargs["name"]
        if name == self.fail_index:
            raise OperationFailure("simulated index creation failure")
        self.indexes[name] = {
            "key": keys,
            **{key: value for key, value in kwargs.items() if key != "name"},
        }
        return name

    def drop_index(self, name):
        self.dropped.append(name)
        self.indexes.pop(name, None)


class FakePosts:
    name = "posts"

    def __init__(self, documents):
        self.documents = documents

    def find(self, query, _projection=None):
        if query == {"publicationKey": {"$exists": False}}:
            return [doc for doc in self.documents if "publicationKey" not in doc]
        raise AssertionError(f"Unexpected find: {query}")

    def find_one(self, query, _projection=None):
        return next(
            (
                doc
                for doc in self.documents
                if all(doc.get(key) == value for key, value in query.items())
            ),
            None,
        )

    def update_one(self, query, update):
        document = self.find_one({"_id": query["_id"]})
        if document is not None:
            document.update(update.get("$set", {}))


class FakeReactionLedger:
    def __init__(self, grouped, replacement_grouped=None):
        self.grouped = grouped
        self.replacement_grouped = replacement_grouped
        self.calls = 0

    def aggregate(self, pipeline, session=None):
        del session
        self.calls += 1
        groups = (
            self.replacement_grouped
            if self.replacement_grouped is not None and self.calls > 1
            else self.grouped
        )
        post_id = next(
            (
                stage["$match"].get("postId")
                for stage in pipeline
                if "$match" in stage and "postId" in stage["$match"]
            ),
            None,
        )
        if post_id is None:
            return groups
        return [group for group in groups if group["_id"]["postId"] == post_id]


class FakeCounterPosts:
    def __init__(self, documents):
        self.documents = documents

    def find(self, _query, _projection=None):
        return self.documents

    def update_one(self, query, update, session=None):
        del session
        post = next(doc for doc in self.documents if doc["_id"] == query["_id"])
        post.update(update["$set"])


class FakeSession:
    def __init__(self, retry=False):
        self.retry = retry

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def with_transaction(self, callback):
        callback(self)
        if self.retry:
            callback(self)


class FakeClient:
    def __init__(self, retry=False):
        self.retry = retry

    def start_session(self):
        return FakeSession(self.retry)


class FakeCounterDatabase:
    def __init__(self, posts, grouped_reactions, replacement_grouped=None, retry=False):
        self.posts = FakeCounterPosts(posts)
        self.reactions = FakeReactionLedger(grouped_reactions, replacement_grouped)
        self.client = FakeClient(retry)


REACTION_KEY = [("postId", 1), ("userId", 1), ("type", 1)]
LEGACY_INDEX = {"legacy_reaction_unique": {"key": REACTION_KEY, "unique": True}}


class MongoIndexSetupTests(unittest.TestCase):
    def test_unexpected_index_failure_is_not_reported_as_success(self):
        collection = FakeCollection(fail_index="critical_index")

        with self.assertRaisesRegex(OperationFailure, "simulated"):
            ensure_index(collection, [("value", 1)], name="critical_index")

    def test_reaction_migration_keeps_a_unique_guard_if_target_creation_fails(self):
        collection = FakeCollection(
            indexes=LEGACY_INDEX,
            fail_index="one_like_per_user",
        )

        with self.assertRaisesRegex(OperationFailure, "simulated"):
            ensure_reaction_indexes(collection)

        self.assertNotIn("legacy_reaction_unique", collection.indexes)
        self.assertIn("reaction_migration_guard", collection.indexes)
        self.assertNotIn("one_like_per_user", collection.indexes)

    def test_reaction_migration_removes_guard_only_after_target_is_verified(self):
        collection = FakeCollection(indexes=LEGACY_INDEX)

        ensure_reaction_indexes(collection)

        self.assertNotIn("reaction_migration_guard", collection.indexes)
        self.assertEqual(
            collection.indexes["one_like_per_user"]["partialFilterExpression"],
            {"type": "like"},
        )
        self.assertIn("post_type", collection.indexes)
        self.assertEqual(
            collection.indexes["one_like_per_network"]["partialFilterExpression"],
            {"type": "like", "rankIdentity": {"$type": "string"}},
        )

    def test_publication_backfill_keeps_one_claim_and_marks_legacy_duplicates(self):
        posted_at = datetime(2026, 7, 13, 9, tzinfo=timezone.utc)
        posts = FakePosts(
            [
                {
                    "_id": "first",
                    "agentHandle": "@agent",
                    "postedAt": posted_at,
                    "project": {"url": "https://example.com/project"},
                },
                {
                    "_id": "second",
                    "agentHandle": "@agent",
                    "postedAt": posted_at,
                    "project": {"url": "https://example.com/project"},
                },
            ]
        )

        backfill_publication_identities(posts)

        claimed = [doc for doc in posts.documents if "publicationKey" in doc]
        duplicates = [doc for doc in posts.documents if "legacyDuplicateOf" in doc]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(duplicates[0]["legacyDuplicateOf"], "first")
        self.assertEqual(duplicates[0]["publicationDay"], "2026-07-13")

    def test_reaction_reconciliation_rebuilds_every_counter_from_events(self):
        posts = [
            {"_id": "post-a", "reactionCounts": {"likes": 99}},
            {"_id": "post-b", "reactionCounts": {"comments": 99}},
        ]
        database = FakeCounterDatabase(
            posts,
            [
                {"_id": {"postId": "post-a", "type": "like"}, "count": 2},
                {"_id": {"postId": "post-a", "type": "share"}, "count": 1},
            ],
        )

        reconciled = reconcile_reaction_counts(database)

        self.assertEqual(reconciled, 2)
        self.assertEqual(
            posts[0]["reactionCounts"], {"likes": 2, "comments": 0, "shares": 1}
        )
        self.assertEqual(
            posts[1]["reactionCounts"], {"likes": 0, "comments": 0, "shares": 0}
        )

    def test_reaction_reconciliation_rereads_the_ledger_on_transaction_retry(self):
        posts = [{"_id": "post-a", "reactionCounts": {"likes": 99}}]
        database = FakeCounterDatabase(
            posts,
            [{"_id": {"postId": "post-a", "type": "like"}, "count": 1}],
            replacement_grouped=[
                {"_id": {"postId": "post-a", "type": "like"}, "count": 3}
            ],
            retry=True,
        )

        reconcile_reaction_counts(database)

        self.assertEqual(
            posts[0]["reactionCounts"], {"likes": 3, "comments": 0, "shares": 0}
        )


if __name__ == "__main__":
    unittest.main()
