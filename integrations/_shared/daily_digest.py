"""Daily digest — the 2-5 most hyped items from the last 24h, ranked by evidence.

Queries MongoDB for external agent posts, scores each on uncapped raw evidence
(star growth per week, relative growth, Hacker News attention, Reddit votes,
views), skips anything featured in the last 30 daily digests, and asks Grove
only to write a blurb for each chosen item. Stores the digest in the digests
collection.
"""

import asyncio
import html
import json
import logging
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared.agent_catalog import AGENT_CATALOG  # noqa: E402
from _shared.engagement import fetch_hn_engagement, fetch_hn_story  # noqa: E402
from _shared.grove import grove_api_key  # noqa: E402
from _shared.momentum import _raw_velocity  # noqa: E402

logger = logging.getLogger(__name__)

DAILY_DIGEST_AGENT_HANDLES = [
    agent["handle"]
    for agent in AGENT_CATALOG
    if agent["handle"] not in ("@community-radar", "@weekly-digest")
]

DAILY_DIGEST_MAX_ITEMS = 5
# A thin day still sends two items (RomBot never trims below two).
DAILY_DIGEST_MIN_ITEMS = 2
DAILY_DIGEST_MAX_PER_AGENT = 3
DAILY_DIGEST_MAX_VIDEOS = 1
# Calibrated on 2026-11-09 posts: real breakouts score 100+, a 1K-view video 36.
DAILY_DIGEST_MIN_SCORE = 50
DAILY_DIGEST_MEMORY_DAYS = 30
# A repo this small when the week began was being born, not breaking out:
# "247 new this week" on a 255-star repo is trivially true (Flide, 2026-11-09),
# while 1.1K -> 11K (auto-ontology) is the story.
_NEW_REPO_START_STARS = 100
_EVIDENCE_CONCURRENCY = 8
_HN_ITEM_ID = re.compile(r"news\.ycombinator\.com/item\?id=(\d+)")
DAILY_DIGEST_WHAT_MAX_CHARS = 140
STAR_SNAPSHOTS_READ = 8

_GITHUB_REPO_URL = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
# A 140-char cut can land mid-list ("... data-backed charts and"); the clause
# must never end on the connective that pointed at the dropped remainder.
_DANGLING_CONNECTIVES = {"and", "or", "with", "for", "&"}
_ABOUT_FETCH_TIMEOUT_SECONDS = 5.0
_ABOUT_PER_REPO_SLEEP_SECONDS = 0.1


def _clean_what(text: object) -> str | None:
    """Decode HTML entities, collapse whitespace, cut at a word boundary to 140."""
    if not isinstance(text, str):
        return None
    collapsed = " ".join(html.unescape(text).split())
    if not collapsed:
        return None
    head = collapsed[: DAILY_DIGEST_WHAT_MAX_CHARS + 1]
    if len(head) > DAILY_DIGEST_WHAT_MAX_CHARS:
        if " " not in head:
            head = head[:DAILY_DIGEST_WHAT_MAX_CHARS]
        else:
            head = head.rsplit(" ", 1)[0]
    while True:
        head = head.rstrip(",;:·—–- ")
        last = head.rsplit(" ", 1)[-1] if head else ""
        if last.lower() in _DANGLING_CONNECTIVES and " " in head:
            head = head.rsplit(" ", 1)[0]
        else:
            break
    return head or None


def _github_owner_repo(url: object) -> tuple[str, str] | None:
    """OWNER/REPO from a strict github.com URL; other hosts and subpaths are skipped.

    Twin of github_radar.github_source._github_owner_repo — keep the two in
    sync: a URL one accepts and the other rejects decides whether a repo is
    published with its description or enriched at digest time.
    """
    if not isinstance(url, str):
        return None
    match = _GITHUB_REPO_URL.match(url.strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def _github_api_headers() -> dict:
    """Same opportunistic token as github_radar: authenticated when the env has it."""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


async def _fetch_github_about_description(
    client: httpx.AsyncClient, owner: str, repo: str
) -> str | None:
    """The repo's own About line, or None on any failure — never a guess."""
    try:
        response = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers=_github_api_headers(),
        )
    except httpx.HTTPError as e:
        logger.warning("GitHub About fetch failed for %s/%s: %s", owner, repo, e)
        return None
    if response.status_code != 200:
        logger.info(
            "GitHub About fetch for %s/%s returned %d", owner, repo, response.status_code
        )
        return None
    try:
        return _clean_what(response.json().get("description"))
    except ValueError:
        return None


async def _enrich_missing_repo_whats(items: list[dict]) -> None:
    """Fill `what` for GitHub repo items whose description is empty.

    The momentum producer historically stored an empty description; this is the
    fail-soft backstop: the author's own About line, fetched once per distinct
    repo, bounded by the digest's own item maximum. Any failure leaves `what`
    null — the renderer says so honestly instead of inventing a description.
    """
    wanted: dict[tuple[str, str], list[dict]] = {}
    for item in items:
        if item.get("kind") != "repo" or item.get("what"):
            continue
        owner_repo = _github_owner_repo(item.get("url"))
        if owner_repo:
            wanted.setdefault(owner_repo, []).append(item)
    if not wanted:
        return
    async with httpx.AsyncClient(
        timeout=_ABOUT_FETCH_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        for (owner, repo), targets in wanted.items():
            # Same guarantee as _weekly_star_gain: enrichment must never cost
            # the digest, so one broken repo leaves its items' what null.
            try:
                description = await _fetch_github_about_description(
                    client, owner, repo
                )
            except Exception as e:
                logger.warning("About backstop skipped for %s/%s: %s", owner, repo, e)
                description = None
            if description:
                for item in targets:
                    item["what"] = description
            await asyncio.sleep(_ABOUT_PER_REPO_SLEEP_SECONDS)


def _item_what(project: dict, grove_what: object) -> str | None:
    """The project's own description wins; Grove's line is only a fallback.

    A video's stored description is a byline, "By <channel> · N views"
    (youtube_trends/source.py), not what the video is, so videos carry no `what`.
    Grove writes its line from the post body, which carries star counts and the
    blurb's growth claims, so a Grove line with any digit in it is dropped: the
    only numbers RomBot may print come from the digest's own numeric fields.
    A GitHub repo without a stored description gets its author's About line via
    _enrich_missing_repo_whats, or nothing — Grove must not guess for it.
    """
    if project.get("kind") == "video":
        return None
    description = _clean_what(project.get("description"))
    if description:
        return description
    if _github_owner_repo(project.get("url")):
        return None
    fallback = _clean_what(grove_what)
    if fallback and not any(char.isdigit() for char in fallback):
        return fallback
    return None


async def _weekly_star_gain(db, project_url: str) -> tuple[int | None, int | None]:
    """Raw star gain over the last daily snapshots and the days they really span.

    Reads the tracker's snapshots the way github_radar does (projectId plus an
    empty postId, oldest first). Both values are None below two snapshots:
    unknown, not zero. The window comes from the timestamps because backfilled
    snapshots are stamped 12:00Z and organic ones at run time. This is
    enrichment: any failure leaves the gain unknown instead of losing the digest.
    """
    try:
        docs = (
            await db.signals.find(
                {"projectId": project_url, "postId": ""},
                {"capturedAt": 1, "github_stars": 1, "_id": 0},
            )
            .sort("capturedAt", 1)
            .to_list(None)
        )
        history = [doc for doc in docs if "github_stars" in doc][-STAR_SNAPSHOTS_READ:]
        if len(history) < 2:
            return None, None
        span = history[-1]["capturedAt"] - history[0]["capturedAt"]
        return _raw_velocity(history, 7), round(span.total_seconds() / 86400)
    except Exception as e:
        logger.warning("Weekly star gain skipped for '%s': %s", project_url, e)
        return None, None


def _daily_post_filter(since: datetime) -> dict:
    """MongoDB filter for posts from external agents in the last 24h, synced."""
    return {
        "postedAt": {"$gte": since},
        "agentHandle": {"$in": DAILY_DIGEST_AGENT_HANDLES},
        "portSyncStatus": "synced",
        "evidenceContractVersion": 2,
        "legacyDuplicateOf": {"$exists": False},
    }


def _build_grove_prompt(posts: list[dict]) -> str:
    """Build the prompt sent to Grove for blurbing the chosen items."""
    items = []
    for i, post in enumerate(posts):
        project = post.get("project", {})
        items.append(
            {
                "id": i,
                "title": project.get("title", ""),
                "agentHandle": post.get("agentHandle", ""),
                "body": post.get("body", ""),
                "verdict": post.get("verdict", ""),
                "rankScore": post.get("rankScore"),
                "momentumScore": project.get("momentumScore"),
            }
        )

    needs_what = [
        i
        for i, post in enumerate(posts)
        if post.get("project", {}).get("kind") != "video"
        and not _clean_what(post.get("project", {}).get("description"))
        and not _github_owner_repo(post.get("project", {}).get("url"))
    ]
    what_request = (
        'For posts with these ids, also return a key "what": a plain line of '
        "at most 12 words saying what the project is, based only on its title "
        "and body, with no numbers, no star counts and no hype words; if the "
        "title and body do not say what it is, return an empty string: "
        f"{json.dumps(needs_what)}. "
        if needs_what
        else ""
    )

    return (
        "You are the HypeRadar editor. "
        "The following posts are already chosen and ordered; do not drop or "
        "reorder any. For every post, write a one-line English blurb "
        "explaining why it's hyped, based only on its title and body. "
        "Return as a JSON array of objects with keys: id, blurb. "
        f"{what_request}"
        f"Posts: {json.dumps(items)}"
    )


async def _call_grove(posts: list[dict]) -> list[dict]:
    """Call Grove LLM to blurb the chosen items. Returns list of {id, blurb}."""
    prompt = _build_grove_prompt(posts)
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{os.environ['GROVE_BASE_URL']}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "api-key": grove_api_key(),
            },
            json={
                "model": os.environ["GROVE_MODEL"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            },
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()

    # Parse JSON — handle markdown code fences
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        result = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Grove returned invalid JSON, returning empty picks")
        return []
    if not isinstance(result, list):
        logger.warning("Grove returned non-list response: %s", type(result))
        return []
    return result


def _post_metrics(post: dict) -> dict:
    """The post's own evidence as ``{metric: value}``; Reddit posts keep comments in delta."""
    signal = post.get("signal") or {}
    metric, value = signal.get("metric"), signal.get("value")
    metrics: dict = {}
    if isinstance(metric, str) and isinstance(value, (int, float)):
        metrics[metric] = value
    if metric == "upvotes" and isinstance(signal.get("delta"), (int, float)):
        metrics["comments"] = signal["delta"]
    return metrics


def _born_this_window(stars: int, weekly_gain: int) -> bool:
    """A repo that started the window under ``_NEW_REPO_START_STARS`` is being born, not breaking out."""
    return stars - weekly_gain < _NEW_REPO_START_STARS


def _evidence_key(post: dict) -> tuple[str, str | None]:
    """Evidence depends on the URL and, for an HN item, on its own story."""
    signal = post.get("signal") or {}
    story_id = _HN_ITEM_ID.search(signal.get("evidenceUrl") or "")
    own_story = story_id[1] if signal.get("metric") == "hn_points" and story_id else None
    return post.get("project", {}).get("url", ""), own_story


def _digest_score(
    metrics: dict,
    weekly_gain: int | None,
    window_days: int | None,
    hn_points: int | None,
    hn_comments: int | None,
) -> float:
    """Uncapped evidence score: log-scaled, so 10,000 stars a week beats 60.

    Star gain is normalized to 7 days because sparse snapshots stretch the
    window (18-54 days on real data). Relative growth rewards a breakout over
    a mega-repo's steady drip, unless the repo started the window under
    ``_NEW_REPO_START_STARS``: a repo being born has no base to break out of.
    """
    score = 0.0
    if weekly_gain is not None and window_days:
        weekly = weekly_gain * 7 / max(window_days, 7)
        stars = metrics.get("github_stars")
        score += 25 * math.log10(1 + weekly)
        if stars and not _born_this_window(stars, weekly_gain):
            score += 15 * min(1.0, weekly / max(stars - weekly, 1))
    if "upvotes" in metrics:
        score += 20 * math.log10(1 + metrics["upvotes"])
        score += 10 * math.log10(1 + metrics.get("comments", 0))
    if "hn_points" in metrics:
        score += 20 * math.log10(1 + metrics["hn_points"])
        score += 10 * math.log10(1 + (hn_comments or 0))
    elif hn_points:
        score += 10 * math.log10(1 + hn_points)
    if "views" in metrics:
        score += 12 * math.log10(1 + metrics["views"])
    return score


async def _recently_featured_urls(db, now: datetime) -> set[str]:
    """URLs in the last 30 days of daily digests, today's own run excluded."""
    try:
        docs = await db.digests.find(
            {
                "digestType": "daily",
                "createdAt": {"$gte": now - timedelta(days=DAILY_DIGEST_MEMORY_DAYS)},
                "date": {"$ne": now.strftime("%Y-%m-%d")},
            },
            {"items.url": 1},
        ).to_list(None)
    except Exception as e:
        logger.warning("Digest memory skipped: %s", e)
        return set()
    return {
        item.get("url")
        for doc in docs
        for item in doc.get("items") or []
        if item.get("url")
    }


async def _evidence(db, post: dict) -> dict:
    """Weekly star gain plus any recent HN story, for scoring and the message."""
    project = post.get("project", {})
    url = project.get("url", "")
    weekly_gain, window_days = await _weekly_star_gain(db, url)
    _, own_story = _evidence_key(post)
    hn = None
    if own_story:
        # An HN item's own story, not the strongest story that mentions the URL.
        hn = await fetch_hn_story(own_story)
    elif project.get("kind") == "repo" and _github_owner_repo(url):
        hn = await fetch_hn_engagement(url)
    return {
        "weeklyGain": weekly_gain,
        "weeklyGainWindowDays": window_days,
        "hnPoints": hn["points"] if hn else None,
        "hnComments": hn["comments"] if hn else None,
    }


def _choose(candidates: list[dict]) -> list[dict]:
    """Strongest first: up to 5 above the floor, never fewer than 2, capped per source."""
    chosen: list[dict] = []
    per_agent: dict[str, int] = {}
    videos = 0
    for candidate in sorted(candidates, key=lambda c: c["score"], reverse=True):
        if len(chosen) >= DAILY_DIGEST_MAX_ITEMS:
            break
        if candidate["score"] < DAILY_DIGEST_MIN_SCORE and len(chosen) >= DAILY_DIGEST_MIN_ITEMS:
            break
        agent = candidate["post"].get("agentHandle", "")
        is_video = candidate["post"].get("project", {}).get("kind") == "video"
        if per_agent.get(agent, 0) >= DAILY_DIGEST_MAX_PER_AGENT:
            continue
        if is_video and videos >= DAILY_DIGEST_MAX_VIDEOS:
            continue
        per_agent[agent] = per_agent.get(agent, 0) + 1
        videos += is_video
        chosen.append(candidate)
    return chosen


def _digest_item(rank: int, candidate: dict, pick: dict) -> dict:
    post, evidence, metrics = candidate["post"], candidate["evidence"], candidate["metrics"]
    project = post.get("project", {})
    signal = post.get("signal", {})
    weekly_gain = evidence["weeklyGain"]
    window_days = evidence["weeklyGainWindowDays"]
    stars = metrics.get("github_stars")
    if weekly_gain is not None and stars and _born_this_window(stars, weekly_gain):
        weekly_gain, window_days = None, None
    return {
        "rank": rank,
        "agentHandle": post.get("agentHandle", ""),
        "title": html.unescape(project.get("title", "")),
        "url": project.get("url", ""),
        "kind": project.get("kind", "repo"),
        "blurb": pick.get("blurb") or post.get("body", "")[:100],
        "what": _item_what(project, pick.get("what")),
        "verdict": post.get("verdict") or None,
        "weeklyGain": weekly_gain,
        "weeklyGainWindowDays": window_days,
        "hnPoints": evidence["hnPoints"],
        "hnComments": evidence["hnComments"],
        "digestScore": round(candidate["score"], 1),
        "score": post.get("rankScore", 0),
        "signalSource": signal.get("source"),
        "signalMetric": signal.get("metric"),
        "signalValue": signal.get("value"),
        "signalDelta": signal.get("delta"),
        "stars": None,
        "velocity": None,
        "contributorCount": None,
    }


async def generate_daily_digest(db=None, now: datetime | None = None) -> dict | None:
    """Generate and store the daily digest. Returns the stored digest doc or None."""
    if now is None:
        now = datetime.now(timezone.utc)

    if db is None:
        from _shared import mongo as _mongo

        db = _mongo._get_db()

    since = now - timedelta(hours=24)
    post_filter = _daily_post_filter(since)

    posts = await db.posts.find(post_filter).sort("rankScore", -1).to_list(50)

    if not posts:
        return None

    featured = await _recently_featured_urls(db, now)
    posts = [p for p in posts if p.get("project", {}).get("url", "") not in featured]
    # One lookup per distinct evidence, run side by side so a slow HN answer
    # cannot add up across 50 posts and push the digest job past its timeout.
    firsts = {_evidence_key(p): p for p in reversed(posts)}
    limit = asyncio.Semaphore(_EVIDENCE_CONCURRENCY)

    async def bounded(post: dict) -> dict:
        async with limit:
            return await _evidence(db, post)

    found = await asyncio.gather(*(bounded(p) for p in firsts.values()))
    evidence_by_key = dict(zip(firsts, found))
    best_by_url: dict[str, dict] = {}
    for post in posts:
        url = post.get("project", {}).get("url", "")
        metrics = _post_metrics(post)
        evidence = evidence_by_key[_evidence_key(post)]
        score = _digest_score(
            metrics,
            evidence["weeklyGain"],
            evidence["weeklyGainWindowDays"],
            evidence["hnPoints"],
            evidence["hnComments"],
        )
        candidate = {"post": post, "metrics": metrics, "evidence": evidence, "score": score}
        if url not in best_by_url or score > best_by_url[url]["score"]:
            best_by_url[url] = candidate

    chosen = _choose(list(best_by_url.values()))

    # Grove writes; it no longer chooses. A Grove outage costs the blurbs
    # (the post body stands in), never the digest.
    try:
        picks = await _call_grove([c["post"] for c in chosen])
    except Exception as e:
        logger.warning("Grove blurbs skipped: %s", e)
        picks = []
    by_id = {
        pick["id"]: pick
        for pick in picks
        if isinstance(pick, dict) and isinstance(pick.get("id"), int)
    }
    items = [
        _digest_item(rank, candidate, by_id.get(rank - 1, {}))
        for rank, candidate in enumerate(chosen, start=1)
    ]

    await _enrich_missing_repo_whats(items)

    date_str = now.strftime("%Y-%m-%d")
    digest = {
        "date": date_str,
        "digestType": "daily",
        "items": items,
        "publicationSyncStatus": "synced",
        "evidenceContractVersion": 2,
        "createdAt": now,
    }

    await db.digests.update_one(
        {"date": date_str, "digestType": "daily"},
        {"$set": digest},
        upsert=True,
    )

    return digest
