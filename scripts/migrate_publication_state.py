"""Converge every stored post through the current publication contract.

Usage:
    set -a && source .env && set +a
    uv run --frozen --project integrations/github_radar python scripts/migrate_publication_state.py
"""

import asyncio
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "integrations"))

from _shared.agent_catalog import AGENT_CATALOG
from _shared.slug import project_slug_for_url, slug_for_url

SAFE_WEEKLY_POST_BODY = (
    "This weekly snapshot connects the strongest synchronized posts from "
    "independent agents. Open the digest to inspect every project and its "
    "source-labeled evidence."
)
SAFE_DIGEST_SUMMARY = (
    "This weekly edit connects synchronized posts from independent agents. "
    "Explore the shared waves, then open each project dossier for source-labeled "
    "evidence."
)


def _digest_rank_score(waves):
    scores = [
        project.get("momentumScore", 0)
        for wave in waves or []
        for project in wave.get("projects", [])
        if not str(project.get("url", "")).startswith("hyperadar://")
    ]
    return round(sum(scores) / len(scores), 1) if scores else 0


def corrected_post_evidence(post, project_topics):
    """Return truthful replacement copy for known pre-contract agent outputs."""
    if post.get("evidenceContractVersion") == 2:
        return None
    handle = post.get("agentHandle")
    summary = post.get("signalsSummary", "")
    if handle == "@github-radar":
        match = re.search(r"stars=([\d,]+),\s*\+([\d.]+)/wk", summary)
        if not match:
            return None
        stars, average = match.groups()
        return {
            "body": (
                f"AVG {average}★/wk since creation. {stars} GitHub stars observed; "
                "recent growth was not independently measured."
            ),
            "signalsSummary": (
                f"GitHub stars={stars.replace(',', '')}; avg since creation={average}/wk; "
                "6-week sustained=not proven"
            ),
        }
    if handle == "@hidden-gems":
        match = re.search(r"stars=([\d,]+)", summary)
        if not match:
            return None
        value = match.group(1).replace(",", "")
        if "hn" in {str(topic).lower() for topic in project_topics}:
            return {
                "body": (
                    f"{value} HN points observed. Early attention—not a GitHub star "
                    "count or a proven trajectory."
                ),
                "signalsSummary": (
                    f"HN points={value}; historical comment count unavailable"
                ),
            }
        return {
            "body": (
                f"{value} GitHub stars observed in the recent-repository scout; "
                "growth trajectory was not measured."
            ),
            "signalsSummary": (
                f"GitHub stars={value}; discovered in recent-repository search"
            ),
        }
    if handle == "@youtube-trends":
        match = re.search(r"views=([\d,]+)", summary)
        if not match:
            return None
        views = match.group(1).replace(",", "")
        return {
            "body": (
                f"{int(views):,} YouTube views observed. Search surfaced this video; "
                "upload-age view velocity was not measured."
            ),
            "signalsSummary": (
                f"YouTube views={views}; historical search position unavailable"
            ),
        }
    if handle == "@reddit-pulse" and summary.startswith("Reddit votes="):
        return {
            "body": (
                "This Reddit demo drew visible attention; the replication discussion "
                "is the useful evidence to inspect."
            ),
            "signalsSummary": (
                "Historical Reddit engagement snapshot; exact count not re-verified"
            ),
        }
    if handle == "@weekly-digest":
        return {
            "body": SAFE_WEEKLY_POST_BODY,
            "signalsSummary": (
                "Weekly digest of synchronized posts; source units normalized in "
                "project dossiers"
            ),
        }
    return None


def _signal_payload(signal):
    return {
        key: value
        for key, value in signal.items()
        if key not in {"_id", "capturedAt", "postId"}
    }


def _assert_receipt_is_quiescent(receipt, now, post_id):
    if not receipt or receipt.get("state") == "complete":
        return
    lease_until = receipt.get("leaseUntil")
    if lease_until is None:
        return
    if not isinstance(lease_until, datetime):
        raise RuntimeError(f"Malformed signal receipt lease for post {post_id}")
    comparable_lease = (
        lease_until.replace(tzinfo=timezone.utc)
        if lease_until.tzinfo is None
        else lease_until
    )
    if comparable_lease > now:
        raise RuntimeError(f"Active signal receipt lease for post {post_id}")


def _legacy_signal_override(signal):
    source = str(signal.get("source", "")).lower()
    metric = str(signal.get("metric", "")).lower()
    value = signal.get("value", 0)
    if source in {"hn", "hacker_news"} and metric in {"stars", "hn_points"}:
        return {
            "source": "hacker_news",
            "metric": "hn_points",
            "value": value,
            "delta": 0,
        }
    if source == "github" and metric in {"stars", "github_stars"}:
        return {
            "source": "github",
            "metric": "github_stars",
            "value": value,
            "delta": 0,
        }
    if source == "youtube" and metric == "views":
        return {
            "source": "youtube",
            "metric": "views",
            "value": value,
            "delta": 0,
        }
    if source in {"reddit", "reddit-serp"} and metric == "search visibility":
        return {
            "source": "reddit",
            "metric": "search_visibility_proxy",
            "value": value,
            "delta": 0,
        }
    if source == "aggregator" and metric == "mentions":
        return {
            "source": "published_post_aggregator",
            "metric": "cross_source_mentions",
            "value": value,
            "delta": 0,
        }
    return None


async def drain_publication_backlog(database, repair_fn=None, batch_size=100):
    """Repair current-contract pending posts without reviving quarantined history."""
    if repair_fn is None:
        from _shared.write_post import repair_pending_posts

        repair_fn = repair_pending_posts

    repaired_total = 0
    repaired_by_agent = {}
    for agent in AGENT_CATALOG:
        handle = agent["handle"]
        repaired_for_agent = 0
        while True:
            remaining = await database.posts.count_documents(
                {
                    "agentHandle": handle,
                    "portSyncStatus": "pending",
                    "evidenceContractVersion": 2,
                }
            )
            if remaining == 0:
                break
            repaired = await repair_fn(
                handle,
                agent["name"],
                agent["bio"],
                agent["source_type"],
                batch_size=batch_size,
            )
            if repaired == 0:
                raise RuntimeError(
                    f"Publication migration stalled with {remaining} pending posts for {handle}"
                )
            repaired_for_agent += repaired
            repaired_total += repaired
        repaired_by_agent[handle] = repaired_for_agent

    remaining_total = await database.posts.count_documents(
        {"portSyncStatus": "pending", "evidenceContractVersion": 2}
    )
    if remaining_total:
        raise RuntimeError(
            f"Publication migration left {remaining_total} posts from unknown agents"
        )
    return {
        "repaired": repaired_total,
        "remaining": remaining_total,
        "byAgent": repaired_by_agent,
    }


async def migrate_signal_provenance(database):
    """Verify legacy signals and reconstruct append receipts for linked signals."""
    legacy_signals = await database.signals.find(
        {"postId": {"$exists": False}}
    ).to_list(length=None)
    verified_legacy = 0
    quarantined_legacy = 0
    now = datetime.now(timezone.utc)
    for signal in legacy_signals:
        project_id = signal.get("projectId")
        signal_override = _legacy_signal_override(signal)
        published_post = None
        if project_id and signal_override:
            published_post = await database.posts.find_one(
                {
                    "project.url": project_id,
                    "portSyncStatus": "synced",
                    "legacyDuplicateOf": {"$exists": False},
                },
                sort=[("postedAt", 1)],
            )
        if not published_post:
            quarantined_legacy += 1
            continue
        await database.legacy_signal_verifications.update_one(
            {"_id": str(signal["_id"])},
            {
                "$set": {
                    "signalId": signal["_id"],
                    "projectId": project_id,
                    "postId": str(published_post["_id"]),
                    "basis": "project-url-match-to-synced-post",
                    "signalOverride": signal_override,
                    "verifiedAt": now,
                }
            },
            upsert=True,
        )
        verified_legacy += 1

    linked_signals = await database.signals.find(
        {"postId": {"$type": "string"}}
    ).to_list(length=None)
    signals_by_post = {}
    for signal in linked_signals:
        signals_by_post.setdefault(signal["postId"], []).append(signal)

    # Validate the complete snapshot before changing a receipt. A lease takeover
    # can legitimately leave more than one physical signal row, but every row
    # linked to a post must still represent the same immutable payload.
    for post_id, signals in signals_by_post.items():
        payload = _signal_payload(signals[0])
        if any(_signal_payload(signal) != payload for signal in signals[1:]):
            raise RuntimeError(f"Signal payload conflict for post {post_id}")
        receipt = await database.signal_receipts.find_one({"_id": post_id})
        if receipt and receipt.get("signal") != payload:
            raise RuntimeError(f"Signal receipt conflict for post {post_id}")
        if receipt and receipt.get("state") == "complete":
            linked_ids = {str(signal["_id"]) for signal in signals}
            if str(receipt.get("signalId")) not in linked_ids:
                raise RuntimeError(
                    f"Completed signal receipt points outside post {post_id}"
                )
        _assert_receipt_is_quiescent(receipt, now, post_id)

    receipts = 0
    orphaned_linked_signals = 0
    for post_id, signals in signals_by_post.items():
        payload = _signal_payload(signals[0])
        await database.signal_receipts.update_one(
            {"_id": post_id},
            {
                "$setOnInsert": {
                    "signal": payload,
                    "createdAt": signals[0].get("capturedAt", now),
                }
            },
            upsert=True,
        )
        receipt = await database.signal_receipts.find_one({"_id": post_id})
        if not receipt or receipt.get("signal") != payload:
            raise RuntimeError(f"Signal receipt conflict for post {post_id}")

        linked_by_id = {str(signal["_id"]): signal for signal in signals}
        if receipt.get("state") == "complete":
            if str(receipt.get("signalId")) not in linked_by_id:
                raise RuntimeError(
                    f"Completed signal receipt points outside post {post_id}"
                )
            receipts += 1
            orphaned_linked_signals += len(signals) - 1
            continue

        _assert_receipt_is_quiescent(receipt, now, post_id)

        canonical_signal = min(
            signals,
            key=lambda signal: (
                str(signal.get("capturedAt", "")),
                str(signal["_id"]),
            ),
        )
        receipt_filter = {"_id": post_id}
        for field in ("state", "leaseEpoch", "leaseOwner", "leaseUntil"):
            receipt_filter[field] = (
                receipt[field] if field in receipt else {"$exists": False}
            )
        result = await database.signal_receipts.update_one(
            receipt_filter,
            {
                "$set": {
                    "state": "complete",
                    "signalId": canonical_signal["_id"],
                    "completedAt": now,
                },
                "$unset": {"leaseOwner": "", "leaseUntil": ""},
            },
        )
        if result.matched_count != 1:
            resolved = await database.signal_receipts.find_one({"_id": post_id})
            if (
                not resolved
                or resolved.get("state") != "complete"
                or resolved.get("signal") != payload
                or str(resolved.get("signalId")) not in linked_by_id
            ):
                raise RuntimeError(
                    f"Signal receipt changed during migration for post {post_id}"
                )
        receipts += 1
        orphaned_linked_signals += len(signals) - 1
    return {
        "verifiedLegacy": verified_legacy,
        "quarantinedLegacy": quarantined_legacy,
        "receipts": receipts,
        "orphanedLinkedSignals": orphaned_linked_signals,
    }


async def migrate_project_identities(
    database,
    sync_project_fn=None,
    sync_post_fn=None,
    delete_project_fn=None,
    delete_post_fn=None,
):
    """Move projects and every Port post relation to collision-resistant IDs."""
    projects = await database.projects.find({}).to_list(length=None)
    route_candidates = {}
    port_retirement_candidates = {}
    for project in projects:
        url = project.get("url")
        if not url:
            continue
        new_identifier = project_slug_for_url(url)
        stored_identifier = project.get("slug") or slug_for_url(url)
        candidates = set(project.get("legacySlugs", []))
        retirement_candidates = set(project.get("retiredPortProjectIds", []))
        if stored_identifier != new_identifier:
            candidates.update((stored_identifier, slug_for_url(url)))
            retirement_candidates.update((stored_identifier, slug_for_url(url)))
        route_candidates[url] = {
            identifier
            for identifier in candidates
            if identifier and identifier != new_identifier
        }
        port_retirement_candidates[url] = {
            identifier
            for identifier in retirement_candidates
            if identifier and identifier != new_identifier
        }
    legacy_counts = Counter(
        identifier
        for candidates in route_candidates.values()
        for identifier in candidates
    )

    migrated = 0
    legacy_identifiers = {
        identifier
        for candidates in port_retirement_candidates.values()
        for identifier in candidates
    }
    for project in projects:
        url = project.get("url")
        if not url:
            continue
        new_identifier = project_slug_for_url(url)
        stored_identifier = project.get("slug") or slug_for_url(url)
        legacy_slugs = sorted(
            identifier
            for identifier in route_candidates.get(url, set())
            if legacy_counts[identifier] == 1
        )
        retired_port_ids = sorted(port_retirement_candidates.get(url, set()))
        identity_is_current = (
            stored_identifier == new_identifier
            and project.get("legacySlugs", []) == legacy_slugs
            and project.get("retiredPortProjectIds", []) == retired_port_ids
        )

        quarantined_posts = await database.posts.find(
            {
                "project.url": url,
                "portSyncStatus": "quarantined",
            }
        ).to_list(length=None)
        if delete_post_fn:
            for post in quarantined_posts:
                delete_post_fn(str(post["_id"]))

        if identity_is_current:
            continue

        updated_project = {
            **project,
            "slug": new_identifier,
            "legacySlugs": legacy_slugs,
            "retiredPortProjectIds": retired_port_ids,
        }
        posts = await database.posts.find(
            {
                "project.url": url,
                "portSyncStatus": "synced",
            }
        ).to_list(length=None)
        if sync_project_fn:
            sync_project_fn(updated_project)
        if sync_post_fn:
            for post in posts:
                sync_post_fn(post)
        await database.projects.update_one(
            {"url": url},
            {
                "$set": {
                    "slug": new_identifier,
                    "legacySlugs": legacy_slugs,
                    "retiredPortProjectIds": retired_port_ids,
                }
            },
        )
        migrated += 1

    retired = 0
    if delete_project_fn:
        for identifier in sorted(legacy_identifiers):
            if delete_project_fn(identifier):
                retired += 1
    return {"migratedProjects": migrated, "retiredProjectEntities": retired}


async def migrate_post_evidence(database, sync_fn=None, delete_fn=None):
    """Stage corrected copy privately, then publish it after the Port twin succeeds."""
    posts = await database.posts.find(
        {
            "$or": [
                {"evidenceContractVersion": {"$ne": 2}},
                {"evidenceCorrectionPending": True},
            ],
            "portSyncStatus": {"$ne": "quarantined"},
        }
    ).to_list(length=None)
    corrected = 0
    quarantined_post_ids = []
    staged_posts = []
    now = datetime.now(timezone.utc)
    for post in posts:
        project = await database.projects.find_one(
            {"url": post.get("project", {}).get("url")}, {"topics": 1}
        )
        correction = (
            {
                "body": post.get("body", ""),
                "signalsSummary": post.get("signalsSummary", ""),
            }
            if post.get("evidenceCorrectionPending")
            else corrected_post_evidence(post, (project or {}).get("topics", []))
        )
        if not correction:
            post_id = str(post["_id"])
            await database.posts.update_one(
                {"_id": post["_id"]},
                {
                    "$set": {
                        "portSyncStatus": "quarantined",
                        "publicationQuarantineReason": "unsupported-legacy-evidence",
                        "publicationQuarantinedAt": now,
                    },
                    "$unset": {"evidenceCorrectionPending": ""},
                },
            )
            quarantined_post_ids.append(post_id)
            continue
        staged = {
            **correction,
            "evidenceContractVersion": 2,
            "evidenceCorrectedAt": now,
            "evidenceCorrectionPending": True,
            "portSyncStatus": "pending",
        }
        await database.posts.update_one({"_id": post["_id"]}, {"$set": staged})
        staged_posts.append({**post, **staged})

    # No Port call is allowed until every legacy row is either a safe staged v2
    # snapshot or an explicit terminal quarantine.
    if delete_fn:
        for post_id in quarantined_post_ids:
            delete_fn(post_id)
    for updated in staged_posts:
        if sync_fn:
            sync_fn(updated)
        result = await database.posts.update_one(
            {
                "_id": updated["_id"],
                "portSyncStatus": "pending",
                "evidenceCorrectionPending": True,
                "evidenceCorrectedAt": updated["evidenceCorrectedAt"],
                "multiSourceSyncStatus": {"$ne": "pending"},
            },
            {
                "$set": {
                    "portSyncStatus": "synced",
                    "portSyncedAt": datetime.now(timezone.utc),
                },
                "$unset": {"evidenceCorrectionPending": ""},
            },
        )
        if result.matched_count != 1:
            raise RuntimeError(
                f"Post {updated['_id']} changed during evidence migration"
            )
        corrected += 1
    return {
        "correctedPosts": corrected,
        "quarantinedPostIds": quarantined_post_ids,
    }


async def assert_migration_quiescence(database):
    """Refuse a global migration while source or reconciliation leases are live."""
    now = datetime.now(timezone.utc)
    active_signal_receipts = await database.signal_receipts.find(
        {
            "state": {"$ne": "complete"},
            "leaseUntil": {"$gt": now},
        },
        {"_id": 1},
    ).to_list(length=20)
    active_project_reconciliations = await database.project_reconcile_leases.find(
        {"leaseUntil": {"$gt": now}},
        {"_id": 1},
    ).to_list(length=20)
    if active_signal_receipts or active_project_reconciliations:
        signal_ids = ", ".join(str(row["_id"]) for row in active_signal_receipts)
        project_ids = ", ".join(
            str(row["_id"]) for row in active_project_reconciliations
        )
        raise RuntimeError(
            "Migration requires idle workers; active signal receipts: "
            f"{signal_ids or 'none'}; active project reconciliations: "
            f"{project_ids or 'none'}"
        )
    return {
        "activeSignalReceipts": 0,
        "activeProjectReconciliations": 0,
    }


async def assert_publication_invariants(database):
    """Fail completion while any publicly readable post bypasses evidence v2."""
    unsafe_posts = await database.posts.find(
        {
            "portSyncStatus": "synced",
            "evidenceContractVersion": {"$ne": 2},
        },
        {"_id": 1},
    ).to_list(length=None)
    if unsafe_posts:
        identifiers = ", ".join(str(post["_id"]) for post in unsafe_posts)
        raise RuntimeError(
            f"Synced posts remain outside evidence contract v2: {identifiers}"
        )
    unsafe_pending = await database.posts.find(
        {
            "portSyncStatus": "pending",
            "evidenceContractVersion": {"$ne": 2},
        },
        {"_id": 1},
    ).to_list(length=None)
    if unsafe_pending:
        identifiers = ", ".join(str(post["_id"]) for post in unsafe_pending)
        raise RuntimeError(
            f"Pending posts remain outside evidence contract v2: {identifiers}"
        )
    unknown_states = await database.posts.find(
        {
            "portSyncStatus": {
                "$nin": ["synced", "pending", "quarantined"],
            }
        },
        {"_id": 1},
    ).to_list(length=None)
    if unknown_states:
        identifiers = ", ".join(str(post["_id"]) for post in unknown_states)
        raise RuntimeError(f"Posts have unknown publication states: {identifiers}")
    return {
        "unsafeSyncedPosts": 0,
        "unsafePendingPosts": 0,
        "unknownPublicationStates": 0,
    }


async def migrate_digest_evidence(database, sync_project_fn=None, sync_post_fn=None):
    """Hide each digest while its corrected MongoDB and Port copies converge."""
    digests = await database.digests.find({}).to_list(length=None)
    corrected = 0
    now = datetime.now(timezone.utc)
    for digest in digests:
        week_id = digest.get("weekId")
        if not week_id:
            continue
        project_url = f"hyperadar://digest/{week_id}"
        project = await database.projects.find_one({"url": project_url})
        posts = await database.posts.find(
            {
                "agentHandle": "@weekly-digest",
                "project.url": project_url,
                "portSyncStatus": {"$ne": "quarantined"},
            }
        ).to_list(length=None)
        if not posts:
            await database.digests.update_one(
                {"weekId": week_id},
                {
                    "$set": {
                        "publicationSyncStatus": "quarantined",
                        "publicationQuarantineReason": "no-publishable-digest-post",
                    }
                },
            )
            continue
        post_ids = [post["_id"] for post in posts]
        summary = (
            digest.get("summary", SAFE_DIGEST_SUMMARY)
            if digest.get("evidenceContractVersion") == 2
            else SAFE_DIGEST_SUMMARY
        )
        rank_score = _digest_rank_score(digest.get("waves", []))
        project_needs_update = bool(
            project
            and (
                project.get("description") != summary
                or project.get("momentumScore") != rank_score
                or project.get("hypeVerdict") != "emerging"
            )
        )
        posts_need_update = any(
            post.get("rankScore") != rank_score
            or post.get("verdict") != "emerging"
            or post.get("portSyncStatus") != "synced"
            or post.get("project", {}).get("description") != summary
            or post.get("project", {}).get("momentumScore") != rank_score
            for post in posts
        )
        digest_needs_update = (
            digest.get("evidenceContractVersion") != 2
            or digest.get("summary") != summary
            or digest.get("rankScore") != rank_score
            or digest.get("publicationSyncStatus") != "synced"
        )
        if not (project_needs_update or posts_need_update or digest_needs_update):
            continue
        updated_project = (
            {
                **project,
                "description": summary,
                "momentumScore": rank_score,
                "hypeVerdict": "emerging",
            }
            if project
            else None
        )
        updated_posts = [
            {
                **post,
                "body": (
                    SAFE_WEEKLY_POST_BODY
                    if post.get("evidenceContractVersion") != 2
                    else post.get("body", SAFE_WEEKLY_POST_BODY)
                ),
                "verdict": "emerging",
                "rankScore": rank_score,
                "baseRankScore": rank_score,
                "portSyncStatus": "pending",
                "evidenceContractVersion": 2,
                "evidenceCorrectedAt": now,
                "project": {
                    **post.get("project", {}),
                    "description": summary,
                    "momentumScore": rank_score,
                    "baseMomentumScore": rank_score,
                    "hypeVerdict": "emerging",
                },
            }
            for post in posts
        ]

        # Gate every public reader before changing any MongoDB snapshot that Port
        # has not accepted yet. The order is restart-safe even without a transaction.
        await database.posts.update_many(
            {"_id": {"$in": post_ids}},
            {"$set": {"portSyncStatus": "pending"}},
        )
        await database.digests.update_one(
            {"weekId": week_id},
            {"$set": {"publicationSyncStatus": "pending"}},
        )

        await database.projects.update_one(
            {"url": project_url},
            {
                "$set": {
                    "description": summary,
                    "momentumScore": rank_score,
                    "hypeVerdict": "emerging",
                }
            },
        )
        await database.posts.update_many(
            {
                "_id": {"$in": post_ids},
                "evidenceContractVersion": {"$ne": 2},
            },
            {"$set": {"body": SAFE_WEEKLY_POST_BODY}},
        )
        await database.posts.update_many(
            {"_id": {"$in": post_ids}},
            {
                "$set": {
                    "verdict": "emerging",
                    "rankScore": rank_score,
                    "baseRankScore": rank_score,
                    "project.description": summary,
                    "project.momentumScore": rank_score,
                    "project.baseMomentumScore": rank_score,
                    "project.hypeVerdict": "emerging",
                    "portSyncStatus": "pending",
                    "evidenceContractVersion": 2,
                    "evidenceCorrectedAt": now,
                }
            },
        )
        await database.digests.update_one(
            {"weekId": week_id},
            {
                "$set": {
                    "summary": summary,
                    "rankScore": rank_score,
                    "evidenceContractVersion": 2,
                    "evidenceCorrectedAt": now,
                    "publicationSyncStatus": "pending",
                    "publicationPostId": (
                        str(posts[0]["_id"]) if posts and posts[0].get("_id") else None
                    ),
                }
            },
        )

        if updated_project and sync_project_fn:
            sync_project_fn(updated_project)
        if sync_post_fn:
            for updated_post in updated_posts:
                sync_post_fn(updated_post)

        synced_at = datetime.now(timezone.utc)
        await database.posts.update_many(
            {
                "_id": {"$in": post_ids},
                "portSyncStatus": "pending",
                "evidenceContractVersion": 2,
            },
            {
                "$set": {
                    "portSyncStatus": "synced",
                    "portSyncedAt": synced_at,
                }
            },
        )
        await database.digests.update_one(
            {"weekId": week_id},
            {
                "$set": {
                    "publicationSyncStatus": "synced",
                    "publicationSyncedAt": synced_at,
                }
            },
        )
        corrected += 1
    return {"correctedDigests": corrected}


async def run():
    from _shared import mongo
    from _shared import port_client

    def sync_port_post(post):
        counts = post.get("reactionCounts", {})
        result = port_client.upsert_post(
            str(post["_id"]),
            post["agentHandle"],
            post["project"]["url"],
            post["body"],
            post.get("verdict", "emerging"),
            post.get("rankScore", 0),
            post.get("postedAt"),
            counts.get("likes", 0),
            counts.get("comments", 0),
            counts.get("shares", 0),
            post.get("signalsSummary", ""),
        )
        port_client.require_success(result, f"evidence correction for {post['_id']}")

    def sync_port_project(project):
        result = port_client.upsert_project(
            project["url"],
            project.get("title", "Weekly Digest"),
            project.get("kind", "site"),
            project.get("description", ""),
            project.get("topics", []),
            project.get("momentumScore", 100),
            project.get("hypeVerdict", "hype looks real"),
        )
        port_client.require_success(
            result, f"digest evidence correction for {project['url']}"
        )

    def delete_port_project(identifier):
        result = port_client.delete_project_entity(identifier)
        if result.get("ok"):
            return result.get("status") != 404
        if result.get("status") == 404:
            return False
        port_client.require_success(result, f"retire project identity {identifier}")
        return False

    def delete_port_post(identifier):
        result = port_client.delete_post_entity(identifier)
        if result.get("ok"):
            return result.get("status") != 404
        if result.get("status") == 404:
            return False
        port_client.require_success(result, f"retire quarantined post {identifier}")
        return False

    try:
        quiescence = await assert_migration_quiescence(mongo.db)
        digests = await migrate_digest_evidence(
            mongo.db, sync_port_project, sync_port_post
        )
        evidence = await migrate_post_evidence(
            mongo.db, sync_port_post, delete_port_post
        )
        safety = await assert_publication_invariants(mongo.db)
        publication = await drain_publication_backlog(mongo.db)
        safety_after_repair = await assert_publication_invariants(mongo.db)
        provenance = await migrate_signal_provenance(mongo.db)
        project_identities = await migrate_project_identities(
            mongo.db,
            sync_port_project,
            sync_port_post,
            delete_port_project,
            delete_port_post,
        )
        return {
            "quiescence": quiescence,
            "digests": digests,
            "evidence": evidence,
            "safety": safety,
            "publication": publication,
            "safetyAfterRepair": safety_after_repair,
            "provenance": provenance,
            "projectIdentities": project_identities,
        }
    finally:
        await mongo.close_client()


def main():
    print(json.dumps(asyncio.run(run()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
