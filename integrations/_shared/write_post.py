"""Shared write path — the twin-model write (MongoDB + Port) for all agents.

Every agent-creator calls write_post() to persist a hype post. A new claim is
private until its Port twins and embedding audit succeed. MongoDB then promotes
the project snapshot and post visibility together in one transaction.

Agent identity (handle, name, bio, source_type) is passed in so each agent
gets its own voice but shares the write infrastructure.
"""

from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import asyncio
import math
import uuid
from urllib.parse import urlparse

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from . import embeddings
from . import mongo
from . import port_client
from .publication_identity import publication_day, publication_key

current_run_id: ContextVar[str | None] = ContextVar(
    "hyperadar_current_run_id", default=None
)

VERDICTS = {"hype looks real", "inflated", "emerging", "cooling"}
PROJECT_KINDS = {"repo", "video", "thread", "site", "discussion"}
PROJECT_RECONCILE_LEASE_SECONDS = 180
PROJECT_RECONCILE_WAIT_SECONDS = 185
PROJECT_RECONCILE_POLL_SECONDS = 0.1


def _bounded_score(value, label: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 100
    ):
        raise ValueError(f"{label} must be a finite number from 0 to 100")


def validate_publication_input(project: dict, verdict: str, rank_score: float) -> None:
    """Reject values that cannot satisfy the MongoDB and Port contracts."""
    if verdict not in VERDICTS:
        raise ValueError(f"Unsupported verdict: {verdict!r}")
    if project.get("kind") not in PROJECT_KINDS:
        raise ValueError(f"Unsupported project kind: {project.get('kind')!r}")
    if not isinstance(project.get("title"), str) or not project["title"].strip():
        raise ValueError("Project title is required")
    if not isinstance(project.get("url"), str) or not project["url"].strip():
        raise ValueError("Project URL is required")
    parsed_url = urlparse(project["url"])
    if parsed_url.scheme not in ("http", "https", "hyperadar") or (
        parsed_url.scheme in ("http", "https") and not parsed_url.netloc
    ):
        raise ValueError(f"Invalid URL scheme: {project['url']}")
    topics = project.get("topics", [])
    if not isinstance(topics, list) or not all(
        isinstance(topic, str) for topic in topics
    ):
        raise ValueError("Project topics must be strings")
    if not isinstance(project.get("description", ""), str):
        raise ValueError("Project description must be text")
    _bounded_score(project.get("momentumScore", 0), "Project momentum score")
    _bounded_score(rank_score, "Post rank score")


def _project_snapshot(project: dict, verdict: str) -> dict:
    return {
        "url": project["url"],
        "title": project["title"],
        "kind": project["kind"],
        "description": project.get("description", ""),
        "topics": project.get("topics", []),
        "momentumScore": project.get("momentumScore", 0),
        "hypeVerdict": verdict,
    }


def _signal_snapshot(signal: dict, source_type: str, project_url: str) -> dict:
    snapshot = {
        "projectId": project_url,
        "source": signal.get("source", source_type),
        "metric": signal.get("metric", "mentions"),
        "value": signal.get("value", 0),
        "delta": signal.get("delta", 0),
    }
    for field in ("evidenceUrl", "evidenceLabel", "sourceQuery"):
        value = signal.get(field)
        if isinstance(value, str) and value.strip():
            snapshot[field] = value.strip()
    return snapshot


async def _ensure_embedding_audit(
    post_id: str, project_url: str, agent_handle: str, embedding: list[float]
) -> None:
    expected = {
        "postId": post_id,
        "projectId": project_url,
        "agentHandle": agent_handle,
        "dims": len(embedding),
        "model": "all-MiniLM-L6-v2",
    }
    await mongo.db.embeddings_audit.update_one(
        {"postId": post_id},
        {
            "$setOnInsert": {
                **expected,
                "recordedAt": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )
    stored = await mongo.db.embeddings_audit.find_one({"postId": post_id})
    if not stored or any(stored.get(key) != value for key, value in expected.items()):
        raise RuntimeError(f"Embedding audit conflict for post {post_id}")


async def _acquire_project_reconcile_lease(project_url: str) -> str:
    owner = str(uuid.uuid4())
    attempts = int(PROJECT_RECONCILE_WAIT_SECONDS / PROJECT_RECONCILE_POLL_SECONDS)
    for _ in range(attempts):
        now = datetime.now(timezone.utc)
        try:
            lease = await mongo.db.project_reconcile_leases.find_one_and_update(
                {
                    "_id": project_url,
                    "$or": [
                        {"leaseUntil": {"$lte": now}},
                        {"leaseUntil": {"$exists": False}},
                        {"leaseOwner": owner},
                    ],
                },
                {
                    "$set": {
                        "leaseOwner": owner,
                        "leaseUntil": now
                        + timedelta(seconds=PROJECT_RECONCILE_LEASE_SECONDS),
                    }
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            lease = None
        if lease and lease.get("leaseOwner") == owner:
            return owner
        await asyncio.sleep(PROJECT_RECONCILE_POLL_SECONDS)
    raise RuntimeError(f"Multi-source reconciliation lease timed out for {project_url}")


async def _release_project_reconcile_lease(project_url: str, owner: str) -> None:
    await mongo.db.project_reconcile_leases.delete_one(
        {"_id": project_url, "leaseOwner": owner}
    )


async def _renew_project_reconcile_lease(project_url: str, owner: str) -> None:
    now = datetime.now(timezone.utc)
    renewed = await mongo.db.project_reconcile_leases.update_one(
        {
            "_id": project_url,
            "leaseOwner": owner,
            "leaseUntil": {"$gt": now},
        },
        {
            "$set": {
                "leaseUntil": now + timedelta(seconds=PROJECT_RECONCILE_LEASE_SECONDS),
            }
        },
    )
    if renewed.matched_count != 1:
        raise RuntimeError(f"Multi-source reconciliation lease lost for {project_url}")


async def _assert_project_reconcile_lease(
    project_url: str, owner: str, session=None
) -> None:
    lease = await mongo.db.project_reconcile_leases.find_one(
        {
            "_id": project_url,
            "leaseOwner": owner,
            "leaseUntil": {"$gt": datetime.now(timezone.utc)},
        },
        session=session,
    )
    if not lease:
        raise RuntimeError(f"Multi-source reconciliation lease lost for {project_url}")


async def _reconcile_lease_heartbeat(
    project_url: str,
    owner: str,
    stop: asyncio.Event,
    failures: list[Exception],
) -> None:
    interval = min(30, PROJECT_RECONCILE_LEASE_SECONDS / 3)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            try:
                await _renew_project_reconcile_lease(project_url, owner)
            except Exception as exc:
                failures.append(exc)
                stop.set()


async def _run_port_call_under_lease(
    project_url: str,
    owner: str,
    heartbeat_failures: list[Exception],
    operation: str,
    function,
    *args,
) -> None:
    await _assert_project_reconcile_lease(project_url, owner)
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        result = await asyncio.shield(worker)
    except asyncio.CancelledError:
        await worker
        raise
    if heartbeat_failures:
        raise heartbeat_failures[0]
    await _assert_project_reconcile_lease(project_url, owner)
    port_client.require_success(result, operation)


async def _reconcile_multi_source(
    project_url: str,
    *,
    current_post_id: str | None = None,
    project_snapshot: dict | None = None,
    embedding: list[float] | None = None,
) -> None:
    owner = await _acquire_project_reconcile_lease(project_url)
    try:
        await _reconcile_multi_source_locked(
            project_url,
            owner,
            current_post_id=current_post_id,
            project_snapshot=project_snapshot,
            embedding=embedding,
        )
    finally:
        await _release_project_reconcile_lease(project_url, owner)


async def _reconcile_multi_source_locked(
    project_url: str,
    owner: str,
    *,
    current_post_id: str | None = None,
    project_snapshot: dict | None = None,
    embedding: list[float] | None = None,
) -> None:
    """Converge Port twins and MongoDB visibility behind one fenced lease."""
    database = mongo.db
    eligible_states = [
        {"portSyncStatus": "synced"},
        {
            "portSyncStatus": "pending",
            "multiSourceSyncStatus": "pending",
        },
    ]
    current_object_id = ObjectId(current_post_id) if current_post_id else None
    if current_object_id is not None:
        eligible_states.append({"_id": current_object_id, "portSyncStatus": "pending"})
    posts = await (
        database.posts.find(
            {
                "project.url": project_url,
                "evidenceContractVersion": 2,
                "legacyDuplicateOf": {"$exists": False},
                "$or": eligible_states,
            }
        )
        .sort("postedAt", -1)
        .to_list(length=None)
    )
    if not posts:
        raise RuntimeError(f"Cannot reconcile missing publication for {project_url}")
    if current_object_id is not None and all(
        post["_id"] != current_object_id for post in posts
    ):
        raise RuntimeError(
            f"Cannot reconcile missing current post {current_post_id} for {project_url}"
        )
    agent_count = len(
        {post.get("agentHandle") for post in posts if post.get("agentHandle")}
    )
    boost = min(max(agent_count - 1, 0) * 10, 20)

    targets = []
    for post in posts:
        previous_boost = post.get("multiSourceBoost", 0)
        explicit_human_bonus = post.get("humanRankBonus")
        if "baseRankScore" in post:
            base_rank = post["baseRankScore"]
            human_bonus = (
                explicit_human_bonus
                if explicit_human_bonus is not None
                else max(post.get("rankScore", 0) - base_rank - previous_boost, 0)
            )
        else:
            human_bonus = explicit_human_bonus or 0
            base_rank = max(post.get("rankScore", 0) - previous_boost - human_bonus, 0)
        human_bonus = min(max(human_bonus, 0), 10)
        embedded_project = post.get("project", {})
        base_momentum = embedded_project.get(
            "baseMomentumScore",
            max(embedded_project.get("momentumScore", 0) - previous_boost, 0),
        )
        targets.append(
            {
                "post": post,
                "baseRankScore": base_rank,
                "humanRankBonus": human_bonus,
                "rankScore": min(base_rank + boost + human_bonus, 100),
                "baseMomentumScore": base_momentum,
                "momentumScore": min(base_momentum + boost, 100),
            }
        )

    source_project = project_snapshot or posts[0].get("project", {})
    if not all(source_project.get(field) for field in ("url", "title", "kind")):
        raise RuntimeError(f"Cannot reconcile incomplete project {project_url}")
    project = _project_snapshot(
        source_project,
        source_project.get("hypeVerdict", posts[0].get("verdict", "emerging")),
    )
    project_score = max(target["momentumScore"] for target in targets)
    project.update(
        {
            "momentumScore": project_score,
            "multiSourceBoost": boost,
            "multiSourceSyncStatus": "synced",
        }
    )
    if embedding is None:
        stored_project = await database.projects.find_one(
            {"url": project_url}, {"embedding": 1}
        )
        embedding = stored_project.get("embedding") if stored_project else None
    if embedding is None:
        embedding = embeddings.embed_project(
            project["title"], project.get("description", ""), project.get("topics", [])
        )

    target_ids = [target["post"]["_id"] for target in targets]

    async def gate_targets(session):
        await _assert_project_reconcile_lease(project_url, owner, session=session)
        now = datetime.now(timezone.utc)
        fenced = await database.project_reconcile_leases.update_one(
            {
                "_id": project_url,
                "leaseOwner": owner,
                "leaseUntil": {"$gt": now},
            },
            {"$set": {"lastFenceAt": now}},
            session=session,
        )
        if fenced.matched_count != 1:
            raise RuntimeError(
                f"Multi-source reconciliation lease lost for {project_url}"
            )
        pending = await database.posts.update_many(
            {"_id": {"$in": target_ids}},
            {
                "$set": {
                    "portSyncStatus": "pending",
                    "multiSourceSyncStatus": "pending",
                }
            },
            session=session,
        )
        if pending.matched_count != len(target_ids):
            raise RuntimeError(f"Cannot gate every multi-source twin for {project_url}")

    async with database.client.start_session() as session:
        await session.with_transaction(gate_targets)

    stop_heartbeat = asyncio.Event()
    heartbeat_failures: list[Exception] = []
    heartbeat = asyncio.create_task(
        _reconcile_lease_heartbeat(
            project_url, owner, stop_heartbeat, heartbeat_failures
        )
    )
    try:
        await _run_port_call_under_lease(
            project_url,
            owner,
            heartbeat_failures,
            f"multi-source project sync for {project_url}",
            port_client.upsert_project,
            project_url,
            project["title"],
            project["kind"],
            project.get("description", ""),
            project.get("topics", []),
            project_score,
            project.get("hypeVerdict", "emerging"),
        )
        for target in targets:
            post = target["post"]
            counts = post.get("reactionCounts", {})
            await _run_port_call_under_lease(
                project_url,
                owner,
                heartbeat_failures,
                f"multi-source post sync for {post['_id']}",
                port_client.upsert_post,
                str(post["_id"]),
                post["agentHandle"],
                project_url,
                post["body"],
                post.get("verdict", "emerging"),
                target["rankScore"],
                post.get("postedAt"),
                counts.get("likes", 0),
                counts.get("comments", 0),
                counts.get("shares", 0),
                post.get("signalsSummary", ""),
            )
    finally:
        stop_heartbeat.set()
        await heartbeat
    if heartbeat_failures:
        raise heartbeat_failures[0]
    await _renew_project_reconcile_lease(project_url, owner)

    async def apply_scores(session):
        await _assert_project_reconcile_lease(project_url, owner, session=session)
        synced_at = datetime.now(timezone.utc)
        await mongo.upsert_project(project, embedding=embedding, session=session)
        for target in targets:
            sync_state = {
                "baseRankScore": target["baseRankScore"],
                "humanRankBonus": target["humanRankBonus"],
                "rankScore": target["rankScore"],
                "multiSourceBoost": boost,
                "project.baseMomentumScore": target["baseMomentumScore"],
                "project.momentumScore": target["momentumScore"],
                "project.multiSourceBoost": boost,
                "portSyncStatus": "synced",
                "portSyncedAt": synced_at,
                "multiSourceSyncStatus": "synced",
            }
            run_id = current_run_id.get()
            if (
                run_id
                and current_object_id is not None
                and target["post"]["_id"] == current_object_id
            ):
                sync_state["portSyncedByRunId"] = run_id
            updated = await database.posts.update_one(
                {
                    "_id": target["post"]["_id"],
                    "portSyncStatus": "pending",
                    "multiSourceSyncStatus": "pending",
                },
                {
                    "$set": sync_state,
                },
                session=session,
            )
            if updated.matched_count != 1:
                raise RuntimeError(
                    f"Cannot publish gated post {target['post']['_id']} for {project_url}"
                )

    async with database.client.start_session() as session:
        await session.with_transaction(apply_scores)


async def _sync_port_twin(
    post_id: str,
    agent_handle: str,
    agent_name: str,
    agent_bio: str,
    source_type: str,
    project: dict,
    embedding: list[float],
) -> None:
    port_client.require_success(
        await asyncio.to_thread(
            port_client.upsert_agent,
            agent_handle,
            agent_name,
            agent_bio,
            source_type,
        ),
        f"agent sync for {agent_handle}",
    )
    await _ensure_embedding_audit(post_id, project["url"], agent_handle, embedding)
    await _reconcile_multi_source(
        project["url"],
        current_post_id=post_id,
        project_snapshot=project,
        embedding=embedding,
    )


async def _repair_existing_post(
    existing: dict,
    agent_handle: str,
    agent_name: str,
    agent_bio: str,
    source_type: str,
    project: dict,
    blurb: str,
    verdict: str,
    rank_score: float,
    signal: dict | None = None,
    prefer_project: bool = False,
) -> str:
    if existing.get("portSyncStatus") == "quarantined":
        raise RuntimeError(
            f"Quarantined publication {existing['_id']} cannot be repaired or republished"
        )
    post_id = str(existing["_id"])
    stored_project = existing.get("project", {})
    project_url = stored_project.get("url") or project.get("url")
    published_project = await mongo.db.projects.find_one({"url": project_url})
    pending = existing.get("portSyncStatus") != "synced"
    if prefer_project:
        merged_project = {**stored_project, **(published_project or {}), **project}
    elif pending:
        merged_project = {**(published_project or {}), **project, **stored_project}
    else:
        merged_project = {**stored_project, **project, **(published_project or {})}
    existing_project = _project_snapshot(
        merged_project,
        merged_project.get("hypeVerdict", existing.get("verdict", verdict)),
    )
    repair_embedding = merged_project.get("embedding")
    if pending and not prefer_project:
        repair_embedding = None
    if not repair_embedding:
        repair_embedding = embeddings.embed_project(
            existing_project["title"],
            existing_project.get("description", ""),
            existing_project.get("topics", []),
        )
    stored_signal = existing.get("signal") or signal
    if stored_signal:
        if "projectId" not in stored_signal:
            stored_signal = {"projectId": existing_project["url"], **stored_signal}
        await mongo.ensure_signal(post_id, stored_signal)
    await _sync_port_twin(
        post_id,
        agent_handle,
        agent_name,
        agent_bio,
        source_type,
        existing_project,
        repair_embedding,
    )
    return post_id


async def repair_pending_posts(
    agent_handle: str,
    agent_name: str,
    agent_bio: str,
    source_type: str,
    batch_size: int = 100,
) -> int:
    """Converge stored pending twins before relying on a fresh source scan."""
    pending_posts = await (
        mongo.db.posts.find(
            {
                "agentHandle": agent_handle,
                "portSyncStatus": "pending",
                "evidenceContractVersion": 2,
            }
        )
        .sort("postedAt", 1)
        .to_list(length=batch_size)
    )
    repaired = 0
    for pending in pending_posts:
        stored_project = pending.get("project", {})
        project_url = stored_project.get("url")
        published_project = await mongo.db.projects.find_one({"url": project_url})
        current_contract = pending.get("evidenceContractVersion") == 2
        project = (
            {**(published_project or {}), **stored_project}
            if current_contract
            else {**stored_project, **(published_project or {})}
        )
        if not all(project.get(field) for field in ("url", "title", "kind")):
            raise RuntimeError(
                f"Cannot repair unpublished post {pending['_id']}: project snapshot is incomplete"
            )
        await _repair_existing_post(
            pending,
            agent_handle,
            agent_name,
            agent_bio,
            source_type,
            project,
            pending.get("body", ""),
            pending.get("verdict", project.get("hypeVerdict", "emerging")),
            pending.get("rankScore", project.get("momentumScore", 0)),
            pending.get("signal"),
            prefer_project=not current_contract,
        )
        repaired += 1
    return repaired


async def write_post(
    agent_handle: str,
    agent_name: str,
    agent_bio: str,
    source_type: str,
    project: dict,
    blurb: str,
    verdict: str,
    signal: dict,
    rank_score: float,
) -> str:
    """Write a hype post to MongoDB + Port. Returns the MongoDB post _id.

    Args:
        agent_handle: e.g. "@github-radar"
        agent_name: e.g. "GitHub Radar"
        agent_bio: agent bio for Port
        source_type: "github" | "reddit" | "youtube" | "web" | "aggregator"
        project: {url, title, kind, description, topics, momentumScore, hypeVerdict}
        blurb: deterministic evidence copy derived from the observed source values
        verdict: "hype looks real" | "inflated" | "emerging" | "cooling"
        signal: {source, metric, value, delta} — the raw hype signal
        rank_score: the post's rank score (momentum-based in v1)
    """
    # 0. Validate every Port-constrained value before creating a pending claim.
    validate_publication_input(project, verdict, rank_score)

    # 1. Embed the project (for $vectorSearch "similar projects")
    embedding = embeddings.embed_project(
        project["title"], project.get("description", ""), project.get("topics", [])
    )

    # 1b. Heal an unfinished twin before applying the daily dedup boundary.
    pending = await mongo.db.posts.find_one(
        {
            "agentHandle": agent_handle,
            "project.url": project["url"],
            "portSyncStatus": "pending",
            "evidenceContractVersion": 2,
        }
    )
    if pending:
        day = publication_day(pending.get("postedAt"))
        pending = await mongo.attach_publication_identity(
            pending["_id"], publication_key(agent_handle, project["url"], day), day
        )
        return await _repair_existing_post(
            pending,
            agent_handle,
            agent_name,
            agent_bio,
            source_type,
            project,
            blurb,
            verdict,
            rank_score,
            signal,
        )

    # 1c. Dedup guard: skip if this agent already posted about this project today.
    start_of_day = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    existing = await mongo.db.posts.find_one(
        {
            "agentHandle": agent_handle,
            "project.url": project["url"],
            "postedAt": {"$gte": start_of_day},
            "portSyncStatus": {"$ne": "quarantined"},
        }
    )
    if existing:
        day = publication_day(existing.get("postedAt"))
        existing = await mongo.attach_publication_identity(
            existing["_id"], publication_key(agent_handle, project["url"], day), day
        )
        return await _repair_existing_post(
            existing,
            agent_handle,
            agent_name,
            agent_bio,
            source_type,
            project,
            blurb,
            verdict,
            rank_score,
            signal,
        )

    # 1d. Retrieve similar episodes for post-decision evidence context.
    #     This is transparent recall, not a learned input to the current verdict.
    from . import episodic_memory

    similar_episodes = await episodic_memory.retrieve_similar_episodes(
        embedding, agent_handle=agent_handle, limit=3
    )
    episodes_context = None
    if similar_episodes:
        episodes_context = [
            {
                "title": e.get("projectTitle", ""),
                "verdict": e.get("verdict", ""),
                "outcome": e.get("outcome", ""),
                "lesson": e.get("lesson", ""),
            }
            for e in similar_episodes
        ]

    # 2. Store a complete private snapshot so retries never depend on a source
    #    item appearing again or mutate the last public project prematurely.
    project_doc = {
        **_project_snapshot(project, verdict),
        "baseMomentumScore": project.get("momentumScore", 0),
    }
    signal_doc = _signal_snapshot(signal, source_type, project["url"])
    day = publication_day()
    post_doc = {
        "agentHandle": agent_handle,
        "body": blurb,
        "verdict": verdict,
        "rankScore": rank_score,
        "baseRankScore": rank_score,
        "project": project_doc,
        "signal": signal_doc,
        "signalsSummary": signal.get(
            "summary", f"{signal.get('metric', 'mentions')}={signal.get('value', 0)}"
        ),
        "portSyncStatus": "pending",
        "publicationDay": day,
        "evidenceContractVersion": 2,
    }
    if episodes_context:
        post_doc["episodesContext"] = episodes_context
    run_id = current_run_id.get()
    if run_id:
        post_doc["runId"] = run_id
    post_id, created = await mongo.claim_post(
        publication_key(agent_handle, project["url"], day), post_doc
    )
    if not created:
        claimed = await mongo.db.posts.find_one({"_id": ObjectId(post_id)})
        if not claimed:
            raise RuntimeError(f"Claimed publication {post_id} disappeared")
        return await _repair_existing_post(
            claimed,
            agent_handle,
            agent_name,
            agent_bio,
            source_type,
            project,
            blurb,
            verdict,
            rank_score,
            signal,
        )
    await mongo.ensure_signal(post_id, signal_doc)

    # 3. Upsert Port entities and audit, then atomically promote the public
    #    MongoDB project snapshot and post status.
    await _sync_port_twin(
        post_id,
        agent_handle,
        agent_name,
        agent_bio,
        source_type,
        project_doc,
        embedding,
    )

    return post_id
