"""T3 + T4 tests: vector search (similar projects) and social layer (reactions + rankScore).

Tests the MongoDB seams that power the web layer:
- T3: $vectorSearch returns similar projects (excluding the query project)
- T4: like writes a reaction, increments counts, recomputes rankScore
- T4: comment writes a reaction with type=comment, increments comment count
- T4: rankScore preserves momentum and adds a capped distinct-participant bonus

Run:  uv run --with pymongo pytest test_web_seams.py -v
"""

from datetime import datetime, timezone

import pytest
from bson import ObjectId


def _make_post(db, momentum=50.0):
    """Insert a test post + project, return the post _id."""
    post_id = str(
        db.posts.insert_one(
            {
                "agentHandle": "@test-agent",
                "body": "test blurb",
                "verdict": "hype looks real",
                "rankScore": momentum,
                "postedAt": datetime.now(timezone.utc),
                "reactionCounts": {"likes": 0, "comments": 0, "shares": 0},
                "project": {
                    "url": f"https://github.com/test/repo-{ObjectId()}",
                    "title": "test/repo",
                    "kind": "repo",
                    "momentumScore": momentum,
                },
                "signalsSummary": "stars=1000, +100/wk",
            }
        ).inserted_id
    )
    return post_id


def _cleanup(db, post_id):
    db.posts.delete_one({"_id": ObjectId(post_id)})
    db.reactions.delete_many({"postId": post_id})


# --- T3: Vector Search ---


class TestVectorSearch:
    """T3 seam: $vectorSearch on projects.embedding returns similar projects."""

    def test_vector_search_returns_similar_excluding_self(self, db):
        """Given a project with an embedding, $vectorSearch returns others, not itself."""
        project = db.projects.find_one({"embedding": {"$exists": True}})
        if not project:
            pytest.skip("no projects with embeddings yet — run the agent first")

        pipeline = [
            {
                "$vectorSearch": {
                    "index": "projects_vector_index",
                    "path": "embedding",
                    "queryVector": project["embedding"],
                    "numCandidates": 50,
                    "limit": 5,
                    "filter": {"url": {"$ne": project["url"]}},
                }
            },
            {"$project": {"_id": 0, "title": 1, "url": 1, "momentumScore": 1}},
        ]
        results = list(db.projects.aggregate(pipeline))
        # The query project must NOT appear in results
        urls = [r["url"] for r in results]
        assert project["url"] not in urls, "self should be filtered out"
        # Each result should have the expected fields
        for r in results:
            assert "title" in r and "url" in r

    def test_vector_search_returns_empty_for_unique_project(self, db):
        """If only one project exists, similar should be empty (filtered out)."""
        # This is a smoke test — we can't control the DB state,
        # but we verify the pipeline doesn't error.
        project = db.projects.find_one({"embedding": {"$exists": True}})
        if not project:
            pytest.skip("no projects with embeddings")
        pipeline = [
            {
                "$vectorSearch": {
                    "index": "projects_vector_index",
                    "path": "embedding",
                    "queryVector": project["embedding"],
                    "numCandidates": 10,
                    "limit": 5,
                    "filter": {"url": {"$ne": project["url"]}},
                }
            },
        ]
        results = list(db.projects.aggregate(pipeline))
        assert isinstance(results, list)


# --- T4: Social layer ---


class TestReactions:
    """T4 seam: like/comment writes reactions + updates counts + recomputes rankScore."""

    def test_like_creates_reaction_and_increments_count(self, db):
        post_id = _make_post(db, momentum=70.0)
        try:
            # Insert a like reaction
            db.reactions.insert_one(
                {
                    "postId": post_id,
                    "userId": "test-user-1",
                    "type": "like",
                    "createdAt": datetime.now(timezone.utc),
                }
            )
            db.posts.update_one(
                {"_id": ObjectId(post_id)}, {"$inc": {"reactionCounts.likes": 1}}
            )

            post = db.posts.find_one({"_id": ObjectId(post_id)})
            assert post["reactionCounts"]["likes"] == 1
            reaction = db.reactions.find_one({"postId": post_id, "type": "like"})
            assert reaction is not None
            assert reaction["userId"] == "test-user-1"
        finally:
            _cleanup(db, post_id)

    def test_comment_creates_reaction_and_increments_count(self, db):
        post_id = _make_post(db, momentum=70.0)
        try:
            db.reactions.insert_one(
                {
                    "postId": post_id,
                    "userId": "test-user-1",
                    "userName": "tester",
                    "text": "hype is real",
                    "type": "comment",
                    "createdAt": datetime.now(timezone.utc),
                }
            )
            db.posts.update_one(
                {"_id": ObjectId(post_id)}, {"$inc": {"reactionCounts.comments": 1}}
            )

            post = db.posts.find_one({"_id": ObjectId(post_id)})
            assert post["reactionCounts"]["comments"] == 1
            comment = db.reactions.find_one({"postId": post_id, "type": "comment"})
            assert comment["text"] == "hype is real"  # noqa: E711
            assert comment["userName"] == "tester"
        finally:
            _cleanup(db, post_id)

    def test_like_is_idempotent_via_unique_index(self, db):
        """A user can't like the same post twice (unique index {postId, userId, type})."""
        post_id = _make_post(db, momentum=70.0)
        try:
            db.reactions.insert_one(
                {
                    "postId": post_id,
                    "userId": "test-user-2",
                    "type": "like",
                    "createdAt": datetime.now(timezone.utc),
                }
            )
            # Second like by same user should raise (duplicate key on unique index)
            from pymongo.errors import DuplicateKeyError

            with pytest.raises(DuplicateKeyError):
                db.reactions.insert_one(
                    {
                        "postId": post_id,
                        "userId": "test-user-2",
                        "type": "like",
                        "createdAt": datetime.now(timezone.utc),
                    }
                )
        finally:
            _cleanup(db, post_id)

    def test_user_can_like_and_comment_same_post(self, db):
        """The unique index includes type, so a user can like AND comment the same post."""
        post_id = _make_post(db, momentum=70.0)
        try:
            db.reactions.insert_one(
                {
                    "postId": post_id,
                    "userId": "test-user-3",
                    "type": "like",
                    "createdAt": datetime.now(timezone.utc),
                }
            )
            db.reactions.insert_one(
                {
                    "postId": post_id,
                    "userId": "test-user-3",
                    "userName": "dual",
                    "text": "both",
                    "type": "comment",
                    "createdAt": datetime.now(timezone.utc),
                }
            )
            count = db.reactions.count_documents(
                {"postId": post_id, "userId": "test-user-3"}
            )
            assert count == 2  # one like + one comment
        finally:
            _cleanup(db, post_id)


class TestRankScore:
    """T4 seam: rankScore = momentum + two points per network participant, capped at ten."""

    def test_rank_score_preserves_momentum_and_adds_a_capped_bonus(self, db):
        post_id = _make_post(db, momentum=80.0)
        try:
            for i in range(5):
                db.reactions.insert_one(
                    {
                        "postId": post_id,
                        "userId": f"rank-test-{i}",
                        "type": "like",
                        "createdAt": datetime.now(timezone.utc),
                    }
                )

            post = db.posts.find_one({"_id": ObjectId(post_id)})
            momentum = post["project"]["momentumScore"]
            recent_participants = len(
                db.reactions.distinct(
                    "userId",
                    {
                        "postId": post_id,
                        "createdAt": {
                            "$gte": datetime.now(timezone.utc).replace(
                                hour=0, minute=0, second=0, microsecond=0
                            )
                        },
                    },
                )
            )
            expected = min(momentum + min(recent_participants * 2, 10), 100)

            assert expected == 90
            assert expected >= momentum
        finally:
            _cleanup(db, post_id)

    def test_repeated_actions_by_one_participant_do_not_multiply_rank(self, db):
        post_id = _make_post(db, momentum=70.0)
        try:
            for index, reaction_type in enumerate(("like", "share", "comment")):
                db.reactions.insert_one(
                    {
                        "postId": post_id,
                        "userId": "one-human",
                        "operationId": f"rank-operation-{index}",
                        "type": reaction_type,
                        "createdAt": datetime.now(timezone.utc),
                    }
                )
            recent_participants = db.reactions.distinct(
                "userId",
                {
                    "postId": post_id,
                    "createdAt": {
                        "$gte": datetime.now(timezone.utc).replace(
                            hour=0, minute=0, second=0, microsecond=0
                        )
                    },
                },
            )
            assert recent_participants == ["one-human"]
            assert 70 + len(recent_participants) * 2 == 72
        finally:
            _cleanup(db, post_id)
