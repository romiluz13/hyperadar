import asyncio
import os
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bson import ObjectId

import migrate_publication_state as migration

from migrate_publication_state import (
    SAFE_DIGEST_SUMMARY,
    corrected_post_evidence,
    drain_publication_backlog,
    migrate_digest_evidence,
    migrate_project_identities,
    migrate_post_evidence,
    migrate_signal_provenance,
)


class FakePosts:
    def __init__(self, pending_by_agent):
        self.pending_by_agent = pending_by_agent

    async def count_documents(self, query):
        handle = query.get("agentHandle")
        if handle:
            return self.pending_by_agent.get(handle, 0)
        return sum(self.pending_by_agent.values())


class FakeDatabase:
    def __init__(self, pending_by_agent):
        self.posts = FakePosts(pending_by_agent)


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    async def to_list(self, length=None):
        return list(self.documents if length is None else self.documents[:length])


class FakeSignals:
    def __init__(self, documents):
        self.documents = documents

    def find(self, query):
        exists = query["postId"].get("$exists")
        if exists is False:
            return FakeCursor([doc for doc in self.documents if "postId" not in doc])
        if query["postId"].get("$type") == "string":
            return FakeCursor(
                [doc for doc in self.documents if isinstance(doc.get("postId"), str)]
            )
        raise AssertionError(f"Unexpected signal query: {query}")


class FakeSyncedPosts:
    def __init__(self, documents):
        self.documents = documents

    async def find_one(self, query, sort=None):
        del sort
        return next(
            (
                post
                for post in self.documents
                if post.get("project", {}).get("url") == query.get("project.url")
                and post.get("portSyncStatus") == query.get("portSyncStatus")
            ),
            None,
        )


class FakeUpsertCollection:
    def __init__(self):
        self.documents = {}

    async def update_one(self, query, update, upsert=False):
        key = query["_id"]
        existing = self.documents.get(key)
        if existing is None and upsert:
            existing = {"_id": key, **update.get("$setOnInsert", {})}
            self.documents[key] = existing
        matches = existing is not None and all(
            field == "_id"
            or (
                isinstance(expected, dict)
                and "$ne" in expected
                and existing.get(field) != expected["$ne"]
            )
            or (
                isinstance(expected, dict)
                and "$exists" in expected
                and (field in existing) is expected["$exists"]
            )
            or existing.get(field) == expected
            for field, expected in query.items()
        )
        if matches:
            existing.update(update.get("$set", {}))
            for field in update.get("$unset", {}):
                existing.pop(field, None)
        return SimpleNamespace(matched_count=int(matches))

    async def find_one(self, query):
        return self.documents.get(query["_id"])


class FakeSignalDatabase:
    def __init__(self, signals, posts):
        self.signals = FakeSignals(signals)
        self.posts = FakeSyncedPosts(posts)
        self.legacy_signal_verifications = FakeUpsertCollection()
        self.signal_receipts = FakeUpsertCollection()


class FakeEvidencePosts:
    def __init__(self, documents):
        self.documents = documents

    def find(self, _query):
        return FakeCursor(self.documents)

    async def update_one(self, query, update):
        post = next(
            (
                document
                for document in self.documents
                if all(
                    (
                        isinstance(expected, dict)
                        and "$ne" in expected
                        and document.get(field) != expected["$ne"]
                    )
                    or document.get(field) == expected
                    for field, expected in query.items()
                )
            ),
            None,
        )
        if post is None:
            return SimpleNamespace(matched_count=0)
        post.update(update["$set"])
        for field in update.get("$unset", {}):
            post.pop(field, None)
        return SimpleNamespace(matched_count=1)


class FakeEvidenceProjects:
    async def find_one(self, _query, _projection=None):
        return {"topics": []}


class FakeEvidenceDatabase:
    def __init__(self, posts):
        self.posts = FakeEvidencePosts(posts)
        self.projects = FakeEvidenceProjects()


class FakeDigestCollection:
    def __init__(self, documents, identity_field):
        self.documents = documents
        self.identity_field = identity_field

    def find(self, _query):
        return FakeCursor(self.documents)

    async def find_one(self, query, _projection=None):
        return next(
            (
                document
                for document in self.documents
                if document.get(self.identity_field) == query.get(self.identity_field)
            ),
            None,
        )

    async def update_one(self, query, update):
        document = await self.find_one(query)
        if document is not None:
            document.update(update["$set"])


class FakeDigestPosts:
    def __init__(self, documents):
        self.documents = documents

    def find(self, query):
        status_filter = query.get("portSyncStatus")

        def status_matches(document):
            if status_filter is None:
                return True
            if isinstance(status_filter, dict) and "$ne" in status_filter:
                return document.get("portSyncStatus") != status_filter["$ne"]
            return document.get("portSyncStatus") == status_filter

        return FakeCursor(
            [
                document
                for document in self.documents
                if document.get("agentHandle") == query.get("agentHandle")
                and document.get("project", {}).get("url") == query.get("project.url")
                and status_matches(document)
            ]
        )

    async def update_many(self, query, update):
        for document in self.documents:
            matches_identity = document.get("agentHandle") == query.get(
                "agentHandle"
            ) and document.get("project", {}).get("url") == query.get("project.url")
            if "_id" in query:
                matches_identity = document.get("_id") in query["_id"]["$in"]
            if matches_identity:
                if (
                    "portSyncStatus" in query
                    and document.get("portSyncStatus") != query["portSyncStatus"]
                ):
                    continue
                contract_query = query.get("evidenceContractVersion")
                contract_filter = (
                    contract_query.get("$ne")
                    if isinstance(contract_query, dict)
                    else None
                )
                if (
                    contract_filter is not None
                    and document.get("evidenceContractVersion") == contract_filter
                ):
                    continue
                expected_contract = contract_query
                if (
                    isinstance(expected_contract, int)
                    and document.get("evidenceContractVersion") != expected_contract
                ):
                    continue
                for field, value in update["$set"].items():
                    target = document
                    parts = field.split(".")
                    for part in parts[:-1]:
                        target = target.setdefault(part, {})
                    target[parts[-1]] = value


class FakeDigestDatabase:
    def __init__(self, digests, projects, posts):
        self.digests = FakeDigestCollection(digests, "weekId")
        self.projects = FakeDigestCollection(projects, "url")
        self.posts = FakeDigestPosts(posts)


class FakeIdentityProjects:
    def __init__(self, documents):
        self.documents = documents

    def find(self, _query):
        return FakeCursor(self.documents)

    async def update_one(self, query, update):
        project = next(doc for doc in self.documents if doc["url"] == query["url"])
        project.update(update["$set"])


class FakeIdentityPosts:
    def __init__(self, documents):
        self.documents = documents

    def find(self, query):
        status_filter = query.get("portSyncStatus")
        allowed_statuses = (
            set(status_filter["$in"])
            if isinstance(status_filter, dict) and "$in" in status_filter
            else {status_filter}
        )
        return FakeCursor(
            [
                post
                for post in self.documents
                if post.get("project", {}).get("url") == query.get("project.url")
                and post.get("portSyncStatus") in allowed_statuses
            ]
        )


class FakeIdentityDatabase:
    def __init__(self, projects, posts):
        self.projects = FakeIdentityProjects(projects)
        self.posts = FakeIdentityPosts(posts)


class PublicationMigrationTests(unittest.TestCase):
    def test_project_identity_migration_moves_relations_before_retiring_collisions(
        self,
    ):
        urls = [
            "https://github.com/foo-bar/baz",
            "https://github.com/foo/bar-baz",
        ]
        projects = [{"url": url, "slug": "foo-bar-baz", "title": url} for url in urls]
        posts = [
            {
                "_id": ObjectId(),
                "portSyncStatus": "synced",
                "project": {"url": url},
            }
            for url in urls
        ]
        database = FakeIdentityDatabase(projects, posts)
        events = []
        retired = set()

        def sync_project(project):
            events.append(("project", project["url"]))

        def sync_post(post):
            events.append(("post", post["project"]["url"]))

        def delete_project(identifier):
            events.append(("delete", identifier))
            if identifier in retired:
                return False
            retired.add(identifier)
            return True

        first = asyncio.run(
            migrate_project_identities(
                database, sync_project, sync_post, delete_project
            )
        )

        self.assertEqual(first, {"migratedProjects": 2, "retiredProjectEntities": 1})
        self.assertNotEqual(projects[0]["slug"], projects[1]["slug"])
        self.assertTrue(
            all(project["slug"].startswith("foo-bar-baz-") for project in projects)
        )
        self.assertTrue(all(project["legacySlugs"] == [] for project in projects))
        self.assertTrue(
            all(
                project["retiredPortProjectIds"] == ["foo-bar-baz"]
                for project in projects
            )
        )
        delete_index = next(
            index for index, event in enumerate(events) if event[0] == "delete"
        )
        self.assertTrue(all(event[0] != "delete" for event in events[:delete_index]))
        self.assertEqual(
            {event[1] for event in events if event[0] == "post"}, set(urls)
        )

        events.clear()
        second = asyncio.run(
            migrate_project_identities(
                database, sync_project, sync_post, delete_project
            )
        )
        self.assertEqual(second, {"migratedProjects": 0, "retiredProjectEntities": 0})
        self.assertEqual(events, [("delete", "foo-bar-baz")])

    def test_project_identity_migration_preserves_only_unambiguous_legacy_links(self):
        project = {
            "url": "https://github.com/unique/project",
            "slug": "unique-project",
            "title": "Unique project",
        }
        database = FakeIdentityDatabase([project], [])

        result = asyncio.run(migrate_project_identities(database))

        self.assertEqual(result["migratedProjects"], 1)
        self.assertEqual(project["legacySlugs"], ["unique-project"])

    def test_project_identity_migration_removes_quarantined_twins_before_delete(self):
        url = "https://github.com/example/quarantined"
        project = {"url": url, "slug": "example-quarantined"}
        quarantined_post = {
            "_id": ObjectId(),
            "portSyncStatus": "quarantined",
            "project": {"url": url},
        }
        database = FakeIdentityDatabase([project], [quarantined_post])
        events = []

        asyncio.run(
            migrate_project_identities(
                database,
                sync_project_fn=lambda _project: events.append("project"),
                sync_post_fn=lambda post: events.append(
                    ("post", post["portSyncStatus"])
                ),
                delete_project_fn=lambda _identifier: events.append("delete") or True,
                delete_post_fn=lambda identifier: events.append(
                    ("delete-post", identifier)
                )
                or True,
            )
        )

        deletion = ("delete-post", str(quarantined_post["_id"]))
        self.assertIn(deletion, events)
        self.assertNotIn(("post", "quarantined"), events)
        self.assertLess(events.index(deletion), events.index("delete"))

    def test_legacy_post_copy_is_rewritten_to_the_evidence_that_was_measured(self):
        github = corrected_post_evidence(
            {
                "agentHandle": "@github-radar",
                "body": "▲ 3.2k★/wk. 6-week sustained growth.",
                "signalsSummary": "stars=4550, +3185.0/wk",
            },
            [],
        )
        hn = corrected_post_evidence(
            {
                "agentHandle": "@hidden-gems",
                "body": "298 stars, Show HN.",
                "signalsSummary": "stars=298, hidden gem",
            },
            ["hn", "hidden-gem"],
        )
        youtube = corrected_post_evidence(
            {
                "agentHandle": "@youtube-trends",
                "body": "45K views fast.",
                "signalsSummary": "views=45752, Google SERP rank=3",
            },
            [],
        )
        weekly = corrected_post_evidence(
            {
                "agentHandle": "@weekly-digest",
                "body": "1k+ upvotes and 2.8k stars/week prove raw velocity.",
                "signalsSummary": "weekly digest for 2026-W27",
            },
            [],
        )

        self.assertIn("avg since creation", github["signalsSummary"])
        self.assertNotIn("sustained growth", github["body"])
        self.assertIn("HN points=298", hn["signalsSummary"])
        self.assertNotIn("stars", hn["body"])
        self.assertIn("YouTube views=45752", youtube["signalsSummary"])
        self.assertNotIn("Google", youtube["signalsSummary"])
        self.assertIn("synchronized posts", weekly["body"])
        self.assertNotIn("upvotes", weekly["body"])
        self.assertNotIn("velocity", weekly["body"])
        self.assertIn("source units", weekly["signalsSummary"])

    def test_evidence_migration_does_not_mark_mongo_complete_before_port(self):
        post = {
            "_id": ObjectId(),
            "agentHandle": "@github-radar",
            "body": "▲ 3.2k★/wk. 6-week sustained growth.",
            "signalsSummary": "stars=4550, +3185.0/wk",
            "project": {"url": "https://github.com/example/project"},
            "portSyncStatus": "synced",
        }
        database = FakeEvidenceDatabase([post])

        def fail_port(_post):
            raise RuntimeError("simulated Port outage")

        with self.assertRaisesRegex(RuntimeError, "Port outage"):
            asyncio.run(migrate_post_evidence(database, fail_port))

        self.assertEqual(post["evidenceContractVersion"], 2)
        self.assertEqual(post["portSyncStatus"], "pending")
        self.assertTrue(post["evidenceCorrectionPending"])

        synced = []
        result = asyncio.run(
            migrate_post_evidence(database, lambda value: synced.append(value.copy()))
        )
        self.assertEqual(result, {"correctedPosts": 1, "quarantinedPostIds": []})
        self.assertEqual(post["portSyncStatus"], "synced")
        self.assertNotIn("evidenceCorrectionPending", post)
        self.assertEqual(synced[0]["body"], post["body"])

    def test_evidence_migration_does_not_overwrite_concurrent_reconciliation(self):
        post = {
            "_id": ObjectId(),
            "agentHandle": "@github-radar",
            "body": "Old growth claim",
            "signalsSummary": "stars=4550, +3185.0/wk",
            "project": {"url": "https://github.com/example/concurrent"},
            "portSyncStatus": "synced",
        }
        database = FakeEvidenceDatabase([post])

        def concurrent_reconciliation(_post):
            post["multiSourceSyncStatus"] = "pending"

        with self.assertRaisesRegex(RuntimeError, "changed during evidence migration"):
            asyncio.run(migrate_post_evidence(database, concurrent_reconciliation))

        self.assertEqual(post["portSyncStatus"], "pending")
        self.assertTrue(post["evidenceCorrectionPending"])
        self.assertEqual(post["multiSourceSyncStatus"], "pending")

    def test_uncorrectable_legacy_evidence_is_quarantined_and_reported(self):
        post = {
            "_id": ObjectId(),
            "agentHandle": "@github-radar",
            "body": "Unverifiable legacy claim",
            "signalsSummary": "legacy format with no recoverable observation",
            "project": {"url": "https://github.com/example/unknown"},
            "portSyncStatus": "synced",
        }
        database = FakeEvidenceDatabase([post])

        deleted = []
        result = asyncio.run(
            migrate_post_evidence(
                database,
                lambda _post: None,
                lambda post_id: deleted.append(post_id) or True,
            )
        )

        self.assertEqual(result["correctedPosts"], 0)
        self.assertEqual(result["quarantinedPostIds"], [str(post["_id"])])
        self.assertEqual(post["portSyncStatus"], "quarantined")
        self.assertEqual(deleted, [str(post["_id"])])
        self.assertEqual(
            post["publicationQuarantineReason"], "unsupported-legacy-evidence"
        )

    def test_every_unsupported_row_is_quarantined_before_the_first_port_call(self):
        correctable = {
            "_id": ObjectId(),
            "agentHandle": "@github-radar",
            "body": "Old growth claim",
            "signalsSummary": "stars=4550, +3185.0/wk",
            "project": {"url": "https://github.com/example/correctable"},
            "portSyncStatus": "synced",
        }
        unsupported = {
            "_id": ObjectId(),
            "agentHandle": "@github-radar",
            "body": "Unsupported legacy claim",
            "signalsSummary": "no recoverable observation",
            "project": {"url": "https://github.com/example/unsupported"},
            "portSyncStatus": "synced",
        }
        database = FakeEvidenceDatabase([correctable, unsupported])

        def sync_first(_post):
            self.assertEqual(unsupported["portSyncStatus"], "quarantined")

        result = asyncio.run(migrate_post_evidence(database, sync_first))

        self.assertEqual(result["correctedPosts"], 1)
        self.assertEqual(result["quarantinedPostIds"], [str(unsupported["_id"])])

    def test_completion_fails_while_any_synced_post_is_outside_evidence_v2(self):
        post_id = ObjectId()

        class Posts:
            def find(self, query, projection):
                self.query = query
                self.projection = projection
                return FakeCursor([{"_id": post_id}])

        class Database:
            posts = Posts()

        with self.assertRaisesRegex(RuntimeError, str(post_id)):
            asyncio.run(migration.assert_publication_invariants(Database()))

        self.assertEqual(Database.posts.query["portSyncStatus"], "synced")
        self.assertEqual(Database.posts.query["evidenceContractVersion"], {"$ne": 2})

    def test_completion_reports_missing_or_unknown_publication_states(self):
        post_id = ObjectId()

        class Posts:
            def find(self, query, _projection):
                if query.get("portSyncStatus") == "synced":
                    return FakeCursor([])
                if "portSyncStatus" in query:
                    return FakeCursor([{"_id": post_id}])
                raise AssertionError(f"Unexpected query: {query}")

        class Database:
            posts = Posts()

        with self.assertRaisesRegex(RuntimeError, str(post_id)):
            asyncio.run(migration.assert_publication_invariants(Database()))

    def test_digest_migration_repairs_every_public_copy_after_port_sync(self):
        project_url = "hyperadar://digest/2026-W28"
        digest = {
            "weekId": "2026-W28",
            "summary": "1k+ upvotes; raw velocity",
            "waves": [
                {
                    "avgMomentum": 100,
                    "projects": [
                        {
                            "url": "hyperadar://digest/2026-W28",
                            "momentumScore": 100,
                        },
                        {
                            "url": "https://example.com/source-one",
                            "momentumScore": 70,
                        },
                        {
                            "url": "https://example.com/source-two",
                            "momentumScore": 75,
                        },
                    ],
                }
            ],
        }
        project = {
            "url": project_url,
            "title": "Weekly Digest — 2026-W28",
            "description": digest["summary"],
            "momentumScore": 100,
            "hypeVerdict": "hype looks real",
        }
        post = {
            "_id": ObjectId(),
            "agentHandle": "@weekly-digest",
            "body": "Unsupported old body",
            "verdict": "hype looks real",
            "rankScore": 100,
            "portSyncStatus": "synced",
            "project": {
                "url": project_url,
                "description": digest["summary"],
                "momentumScore": 100,
            },
        }
        database = FakeDigestDatabase([digest], [project], [post])
        synced_projects = []
        synced_posts = []

        result = asyncio.run(
            migrate_digest_evidence(
                database,
                lambda value: synced_projects.append(value.copy()),
                lambda value: synced_posts.append(value.copy()),
            )
        )

        self.assertEqual(result, {"correctedDigests": 1})
        self.assertEqual(synced_projects[0]["description"], SAFE_DIGEST_SUMMARY)
        self.assertEqual(synced_projects[0]["momentumScore"], 72.5)
        self.assertEqual(synced_posts[0]["rankScore"], 72.5)
        self.assertEqual(digest["summary"], SAFE_DIGEST_SUMMARY)
        self.assertEqual(digest["rankScore"], 72.5)
        self.assertEqual(project["description"], SAFE_DIGEST_SUMMARY)
        self.assertEqual(project["momentumScore"], 72.5)
        self.assertEqual(post["project"]["description"], SAFE_DIGEST_SUMMARY)
        self.assertEqual(post["rankScore"], 72.5)
        self.assertEqual(post["verdict"], "emerging")
        self.assertEqual(digest["evidenceContractVersion"], 2)

    def test_digest_migration_hides_every_web_copy_during_a_port_failure(self):
        project_url = "hyperadar://digest/2026-W29"
        digest = {
            "weekId": "2026-W29",
            "summary": "Source-only summary",
            "waves": [
                {
                    "projects": [
                        {
                            "url": "https://example.com/source",
                            "momentumScore": 64,
                        }
                    ]
                }
            ],
            "evidenceContractVersion": 2,
            "publicationSyncStatus": "synced",
            "rankScore": 100,
        }
        project = {
            "url": project_url,
            "title": "Weekly Digest — 2026-W29",
            "description": digest["summary"],
            "momentumScore": 100,
            "hypeVerdict": "emerging",
        }
        post = {
            "_id": ObjectId(),
            "agentHandle": "@weekly-digest",
            "body": SAFE_DIGEST_SUMMARY,
            "verdict": "emerging",
            "rankScore": 100,
            "portSyncStatus": "synced",
            "evidenceContractVersion": 2,
            "project": {"url": project_url, "momentumScore": 100},
        }
        database = FakeDigestDatabase([digest], [project], [post])

        def fail_port(_value):
            raise RuntimeError("simulated Port outage")

        with self.assertRaisesRegex(RuntimeError, "Port outage"):
            asyncio.run(migrate_digest_evidence(database, fail_port, fail_port))

        self.assertEqual(digest["publicationSyncStatus"], "pending")
        self.assertEqual(post["portSyncStatus"], "pending")
        self.assertEqual(post["rankScore"], 64)

        result = asyncio.run(
            migrate_digest_evidence(database, lambda _value: None, lambda _value: None)
        )
        self.assertEqual(result, {"correctedDigests": 1})
        self.assertEqual(digest["publicationSyncStatus"], "synced")
        self.assertEqual(post["portSyncStatus"], "synced")

    def test_digest_migration_gates_every_public_copy_before_mutating_fields(self):
        project_url = "hyperadar://digest/2026-W30"
        digest = {
            "weekId": "2026-W30",
            "summary": "Unsupported old summary",
            "waves": [],
            "publicationSyncStatus": "synced",
        }
        project = {
            "url": project_url,
            "description": "Unsupported old summary",
            "momentumScore": 100,
            "hypeVerdict": "hype looks real",
        }
        post = {
            "_id": ObjectId(),
            "agentHandle": "@weekly-digest",
            "body": "Unsupported old body",
            "portSyncStatus": "synced",
            "project": {"url": project_url},
        }
        database = FakeDigestDatabase([digest], [project], [post])
        original_update = database.projects.update_one

        async def guarded_project_update(query, update):
            self.assertEqual(post["portSyncStatus"], "pending")
            self.assertEqual(digest["publicationSyncStatus"], "pending")
            await original_update(query, update)

        database.projects.update_one = guarded_project_update

        asyncio.run(
            migrate_digest_evidence(database, lambda _value: None, lambda _value: None)
        )

        self.assertEqual(digest["publicationSyncStatus"], "synced")

    def test_digest_migration_never_promotes_a_post_created_after_its_snapshot(self):
        project_url = "hyperadar://digest/2026-W31"
        digest = {
            "weekId": "2026-W31",
            "summary": "Unsupported old summary",
            "waves": [],
            "publicationSyncStatus": "synced",
        }
        project = {
            "url": project_url,
            "description": digest["summary"],
            "momentumScore": 100,
            "hypeVerdict": "hype looks real",
        }
        captured_post = {
            "_id": ObjectId(),
            "agentHandle": "@weekly-digest",
            "body": "Unsupported old body",
            "portSyncStatus": "synced",
            "project": {"url": project_url},
        }
        concurrent_post = {
            "_id": ObjectId(),
            "agentHandle": "@weekly-digest",
            "body": "A concurrently staged digest",
            "portSyncStatus": "pending",
            "evidenceContractVersion": 2,
            "project": {"url": project_url},
        }
        database = FakeDigestDatabase([digest], [project], [captured_post])

        def sync_project(_value):
            database.posts.documents.append(concurrent_post)

        asyncio.run(
            migrate_digest_evidence(database, sync_project, lambda _value: None)
        )

        self.assertEqual(captured_post["portSyncStatus"], "synced")
        self.assertEqual(concurrent_post["portSyncStatus"], "pending")
        self.assertEqual(concurrent_post["body"], "A concurrently staged digest")

    def test_digest_migration_never_revives_a_terminally_quarantined_post(self):
        project_url = "hyperadar://digest/2026-W32"
        digest = {
            "weekId": "2026-W32",
            "summary": "Unsafe historical summary",
            "waves": [],
            "publicationSyncStatus": "synced",
        }
        project = {
            "url": project_url,
            "description": digest["summary"],
            "momentumScore": 100,
            "hypeVerdict": "hype looks real",
        }
        quarantined_post = {
            "_id": ObjectId(),
            "agentHandle": "@weekly-digest",
            "body": "Terminally unsafe digest copy",
            "portSyncStatus": "quarantined",
            "evidenceContractVersion": 2,
            "project": {"url": project_url},
        }
        database = FakeDigestDatabase([digest], [project], [quarantined_post])
        synced_posts = []

        result = asyncio.run(
            migrate_digest_evidence(
                database,
                lambda _value: None,
                lambda value: synced_posts.append(value),
            )
        )

        self.assertEqual(result, {"correctedDigests": 0})
        self.assertEqual(quarantined_post["portSyncStatus"], "quarantined")
        self.assertEqual(quarantined_post["body"], "Terminally unsafe digest copy")
        self.assertEqual(digest["publicationSyncStatus"], "quarantined")
        self.assertEqual(synced_posts, [])

    def test_drain_loops_past_one_batch_and_includes_blocked_agents(self):
        pending = {"@reddit-pulse": 205}
        database = FakeDatabase(pending)
        calls = []

        async def repair(handle, _name, _bio, _source_type, batch_size=100):
            calls.append(handle)
            repaired = min(batch_size, pending.get(handle, 0))
            pending[handle] = pending.get(handle, 0) - repaired
            return repaired

        result = asyncio.run(drain_publication_backlog(database, repair_fn=repair))

        self.assertEqual(result["repaired"], 205)
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(calls.count("@reddit-pulse"), 3)

    def test_drain_repairs_only_current_contract_pending_posts(self):
        queries = []

        class Posts:
            async def count_documents(self, query):
                queries.append(query)
                return 0

        class Database:
            posts = Posts()

        asyncio.run(drain_publication_backlog(Database(), repair_fn=AsyncMock()))

        self.assertTrue(queries)
        for query in queries:
            self.assertEqual(query["portSyncStatus"], "pending")
            self.assertEqual(query["evidenceContractVersion"], 2)

    def test_run_corrects_and_checks_evidence_before_draining_backlog(self):
        with patch.dict(
            os.environ,
            {
                "MONGODB_URI": "mongodb://localhost:27017",
                "PORT_CLIENT_ID": "test-client",
                "PORT_CLIENT_SECRET": "test-secret",
            },
        ):
            from _shared import mongo

            events = []

            def step(name, result):
                events.append(name)
                return result

            with (
                patch.object(mongo, "_get_db", return_value=object()),
                patch.object(mongo, "close_client", new=AsyncMock()),
                patch.object(
                    migration,
                    "assert_migration_quiescence",
                    side_effect=lambda *_args: step("quiescence", {}),
                    create=True,
                ),
                patch.object(
                    migration,
                    "migrate_digest_evidence",
                    side_effect=lambda *_args: step("digests", {}),
                ),
                patch.object(
                    migration,
                    "migrate_post_evidence",
                    side_effect=lambda *_args: step("evidence", {}),
                ),
                patch.object(
                    migration,
                    "assert_publication_invariants",
                    side_effect=lambda *_args: step("invariants", {}),
                    create=True,
                ),
                patch.object(
                    migration,
                    "drain_publication_backlog",
                    side_effect=lambda *_args: step("drain", {}),
                ),
                patch.object(
                    migration,
                    "migrate_signal_provenance",
                    side_effect=lambda *_args: step("provenance", {}),
                ),
                patch.object(
                    migration,
                    "migrate_project_identities",
                    side_effect=lambda *_args: step("identities", {}),
                ),
            ):
                asyncio.run(migration.run())

        self.assertEqual(
            events[:5],
            ["quiescence", "digests", "evidence", "invariants", "drain"],
        )

    def test_migration_preflight_rejects_active_signal_and_project_leases(self):
        now = migration.datetime.now(migration.timezone.utc)

        class LeaseCollection:
            def __init__(self, documents):
                self.documents = documents

            def find(self, _query, _projection):
                return FakeCursor(self.documents)

        class Database:
            signal_receipts = LeaseCollection([{"_id": "signal-post"}])
            project_reconcile_leases = LeaseCollection(
                [{"_id": "https://example.com/project"}]
            )

        with patch.object(migration, "datetime") as mocked_datetime:
            mocked_datetime.now.return_value = now
            with self.assertRaisesRegex(
                RuntimeError, "signal-post.*example.com/project"
            ):
                asyncio.run(migration.assert_migration_quiescence(Database()))

    def test_signal_migration_verifies_only_public_provenance_and_repairs_receipts(
        self,
    ):
        project_url = "https://example.com/published"
        legacy_signal_id = ObjectId()
        reddit_signal_id = ObjectId()
        orphan_signal_id = ObjectId()
        linked_signal_id = ObjectId()
        database = FakeSignalDatabase(
            [
                {
                    "_id": legacy_signal_id,
                    "projectId": project_url,
                    "source": "hn",
                    "metric": "stars",
                    "value": 1,
                },
                {
                    "_id": reddit_signal_id,
                    "projectId": project_url,
                    "source": "reddit-serp",
                    "metric": "search visibility",
                    "value": 70,
                },
                {
                    "_id": orphan_signal_id,
                    "projectId": "https://example.com/orphan",
                    "source": "hn",
                    "metric": "stars",
                    "value": 2,
                },
                {
                    "_id": linked_signal_id,
                    "postId": "published-post",
                    "projectId": project_url,
                    "metric": "linked",
                    "value": 3,
                },
            ],
            [
                {
                    "_id": ObjectId(),
                    "project": {"url": project_url},
                    "portSyncStatus": "synced",
                    "postedAt": "2026-07-13T00:00:00Z",
                }
            ],
        )

        result = asyncio.run(migrate_signal_provenance(database))

        self.assertEqual(
            result,
            {
                "verifiedLegacy": 2,
                "quarantinedLegacy": 1,
                "receipts": 1,
                "orphanedLinkedSignals": 0,
            },
        )
        verification = database.legacy_signal_verifications.documents[
            str(legacy_signal_id)
        ]
        self.assertEqual(verification["signalId"], legacy_signal_id)
        self.assertEqual(verification["basis"], "project-url-match-to-synced-post")
        self.assertEqual(
            verification["signalOverride"],
            {
                "source": "hacker_news",
                "metric": "hn_points",
                "value": 1,
                "delta": 0,
            },
        )
        self.assertEqual(
            database.legacy_signal_verifications.documents[str(reddit_signal_id)][
                "signalOverride"
            ],
            {
                "source": "reddit",
                "metric": "search_visibility_proxy",
                "value": 70,
                "delta": 0,
            },
        )
        receipt = database.signal_receipts.documents["published-post"]
        self.assertEqual(receipt["state"], "complete")
        self.assertEqual(receipt["signalId"], linked_signal_id)

    def test_signal_migration_preserves_completed_receipt_after_lease_takeover(self):
        post_id = "takeover-post"
        first_signal_id = ObjectId()
        canonical_signal_id = ObjectId()
        payload = {
            "postId": post_id,
            "projectId": "https://example.com/takeover",
            "source": "github",
            "metric": "github_stars",
            "value": 42,
            "delta": 2,
        }
        database = FakeSignalDatabase(
            [
                {"_id": first_signal_id, **payload},
                {"_id": canonical_signal_id, **payload},
            ],
            [],
        )
        database.signal_receipts.documents[post_id] = {
            "_id": post_id,
            "signal": migration._signal_payload(
                {"_id": canonical_signal_id, **payload}
            ),
            "state": "complete",
            "signalId": canonical_signal_id,
        }

        result = asyncio.run(migrate_signal_provenance(database))

        self.assertEqual(result["receipts"], 1)
        self.assertEqual(result["orphanedLinkedSignals"], 1)
        self.assertEqual(
            database.signal_receipts.documents[post_id]["signalId"],
            canonical_signal_id,
        )

    def test_signal_migration_validates_every_lease_before_writing_any_receipt(self):
        now = migration.datetime.now(migration.timezone.utc)
        first = {
            "_id": ObjectId(),
            "postId": "first-post",
            "projectId": "https://example.com/first",
            "source": "github",
            "metric": "github_stars",
            "value": 1,
        }
        leased = {
            "_id": ObjectId(),
            "postId": "leased-post",
            "projectId": "https://example.com/leased",
            "source": "youtube",
            "metric": "views",
            "value": 2,
        }
        database = FakeSignalDatabase([first, leased], [])
        database.signal_receipts.documents["leased-post"] = {
            "_id": "leased-post",
            "signal": migration._signal_payload(leased),
            "state": "pending",
            "leaseUntil": now + timedelta(minutes=5),
        }

        with self.assertRaisesRegex(RuntimeError, "Active signal receipt lease"):
            asyncio.run(migrate_signal_provenance(database))

        self.assertNotIn("first-post", database.signal_receipts.documents)


if __name__ == "__main__":
    unittest.main()
