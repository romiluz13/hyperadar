"""Reddit source — each subreddit's rising feed, fetched for free through Monid.

Monid's TinyFish ``/free/fetch`` endpoint ($0 per call, 1,000 URLs/day) loads
Reddit's rising-feed markup, whose ``<shreddit-post>`` elements carry the score,
comment count and creation time. Reddit's JSON listings answer 403 to it, and
the earlier Bright Data pipeline was paid; see ADR 0006.

Requires: ``MONID_API_KEY`` (a Monid API key; the free endpoint bills $0).
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx

from _shared.heat import compute_heat_score, should_publish_heat
from _shared.mongo import _get_db

# Target subreddits for AI developer discourse.
SUBREDDITS = [
    "LocalLLaMA",
    "LLMDevs",
    "AI_Agents",
    "ClaudeAI",
    "ChatGPTCoding",
    "artificial",
    "ExperiencedDevs",
    "softwarearchitecture",
    "devops",
    "mongodb",
    "LangChain",
    "Rag",
    "SaaS",
    "startups",
    "singularity",
    "MachineLearning",
]
MONID_API_URL = "https://api.monid.ai"
MONID_FETCH_BATCH = 10  # the free fetch takes 1-10 URLs per call
MONID_HTTP_TIMEOUT_SECONDS = 120
MONID_RUN_DEADLINE_SECONDS = 180
MONID_POLL_INTERVAL_SECONDS = 3
COOLDOWN_DAYS = 7
_PERMALINK = re.compile(r"^/r/([^/]+)/comments/([^/]+)")


def _visibility_from_upvotes(upvotes: int, comments: int) -> float:
    """Map upvotes + comments to a 0-100 visibility score.

    Upvotes dominate; comments add discourse signal. Capped at 100.
    """
    score = min(upvotes / 10, 80) + min(comments / 5, 20)
    return round(max(score, 20), 1)


def _normalize_reddit_url(url: str) -> str:
    """Normalize a Reddit URL for dedup/cooldown matching.

    Strips query params and trailing slashes so that:
    - https://www.reddit.com/r/LocalLLaMA/comments/abc/
    - https://www.reddit.com/r/LocalLLaMA/comments/abc/?utm_source=share
    - https://www.reddit.com/r/LocalLLaMA/comments/abc
    all match the same post.
    """
    # Strip query params
    if "?" in url:
        url = url.split("?")[0]
    # Strip trailing slash
    url = url.rstrip("/")
    return url


def _post_age_hours(post: dict) -> float:
    """Hours since the Reddit post was created. Falls back to 1 if unknown.

    A post without a creation time scores velocity as upvotes/1 = upvotes, so
    the ranking degrades to sort-by-upvotes rather than failing.
    """
    for key in ("created_utc", "created_at"):
        raw = post.get(key)
        if raw is None:
            continue
        try:
            if isinstance(raw, (int, float)):
                created = datetime.fromtimestamp(raw, tz=timezone.utc)
            else:
                created = datetime.fromisoformat(str(raw))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
        except (ValueError, OSError, OverflowError):
            continue
        return max(1.0, (datetime.now(timezone.utc) - created).total_seconds() / 3600)
    return 1.0


async def _last_posted_days(db, project_url: str) -> int:
    """Days since the most recent post for this project URL. 999 if never posted."""
    normalized = _normalize_reddit_url(project_url)
    post = await db.posts.find_one(
        {"project.url": normalized},
        {"postedAt": 1},
        sort=[("postedAt", -1)],
    )
    if not post or not post.get("postedAt"):
        return 999
    posted = post["postedAt"]
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - posted
    return max(0, delta.days)


async def _save_reddit_snapshot(
    db,
    url: str,
    upvotes: int,
    comments: int,
    title: str = "",
    description: str = "",
    subreddit: str = "",
) -> None:
    """Save a per-run engagement snapshot for delta-based discovery.

    The snapshot doubles as the cross-source corroboration corpus: title +
    description let the @github-radar gate match repos mentioned in threads
    (including threads that never became posts), so the text fields are part
    of the contract, not decoration.
    """
    await db.reddit_post_snapshots.insert_one(
        {
            "url": _normalize_reddit_url(url),
            "upvotes": upvotes,
            "comments": comments,
            "title": title[:200],
            "description": description[:500],
            "subreddit": subreddit,
            "capturedAt": datetime.now(timezone.utc),
        }
    )


async def _last_reddit_snapshot(db, url: str) -> dict | None:
    """Get the most recent per-run snapshot for a URL."""
    return await db.reddit_post_snapshots.find_one(
        {"url": _normalize_reddit_url(url)},
        sort=[("capturedAt", -1)],
    )


def _parse_created(raw: str | None) -> str | None:
    """Reddit renders ``2026-11-09T22:22:35.915000+0000``; keep it ISO-parseable."""
    if not raw:
        return None
    return raw[:-5] + raw[-5:-2] + ":" + raw[-2:] if len(raw) > 5 and raw[-5] in "+-" else raw


class _FeedParser(HTMLParser):
    """Collect each ``<shreddit-post>``'s attributes and its text body, by post id."""

    def __init__(self) -> None:
        super().__init__()
        self.posts: dict[str, dict] = {}
        self.bodies: dict[str, list[str]] = {}
        self._body_id: str | None = None
        self._div_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "shreddit-post":
            self._body_id = None  # a body never outlives its post, even when left open
            post_id = values.get("id", "")
            if post_id and post_id not in self.posts:
                self.posts[post_id] = values
        elif self._body_id is not None:
            self._div_depth += tag == "div"
        elif values.get("property") == "schema:articleBody" and tag == "div":
            post_id = values.get("about", "")
            # The cleaned markup repeats fragments; the first body per post wins.
            if post_id and post_id not in self.bodies:
                self.bodies[post_id] = []
                self._body_id, self._div_depth = post_id, 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "shreddit-post":
            self._body_id = None
        elif self._body_id is not None and tag == "div":
            self._div_depth -= 1
            if self._div_depth == 0:
                self._body_id = None

    def handle_data(self, data: str) -> None:
        if self._body_id is not None and data.strip():
            self.bodies[self._body_id].append(data.strip())


def parse_rising_feed(markup: str) -> list[dict]:
    """Candidate dicts from Reddit's rising-feed markup, one per post.

    The feed carries engagement as attributes of each ``<shreddit-post>``
    (``score``, ``comment-count``, ``created-timestamp``); posts with no
    permalink or title cannot be cited and are dropped. Raises ValueError when
    no post in the markup can be read at all (an empty page or renamed
    attributes): that is a broken source, not a quiet subreddit.
    """
    parser = _FeedParser()
    parser.feed(markup)
    results = []
    readable = 0
    for post_id, values in parser.posts.items():
        title = values.get("post-title", "").strip()
        permalink = values.get("permalink", "").strip()
        if not title or not permalink:
            continue
        identity = _PERMALINK.match(permalink)
        if not identity:
            continue
        # Same canonical form as every earlier post and snapshot (no slug), so
        # cooldown and upvote-delta lookups keep matching history.
        url = f"https://www.reddit.com/r/{identity[1]}/comments/{identity[2]}"
        subreddit = values.get("subreddit-name", "").strip() or identity[1]
        try:
            upvotes = int(values["score"])
            comments = int(values.get("comment-count") or 0)
        except (KeyError, ValueError):
            continue
        readable += 1
        if upvotes < 10:  # filter noise
            continue
        link = values.get("content-href", "").strip()
        parts = [title, " ".join(parser.bodies.get(post_id, []))]
        if link and "reddit.com" not in link:
            parts.append(link)
        description = " ".join(part for part in parts if part)
        age_hours = _post_age_hours(
            {"created_at": _parse_created(values.get("created-timestamp"))}
        )
        results.append(
            {
                "url": url,
                "title": title[:200],
                "kind": "thread",
                "description": description[:500],
                "topics": ["reddit", "ai", subreddit],
                "subreddit": subreddit,
                "num_upvotes": upvotes,
                "num_comments": comments,
                "age_hours": age_hours,
                "serp_rank": 1,  # back-compat: no longer SERP, keep field
                "visibility_score": _visibility_from_upvotes(upvotes, comments),
                "engagement_velocity": round(upvotes / max(age_hours, 1), 2),
                "evidence_url": url,
            }
        )
    if not readable:
        raise ValueError("no posts in the feed markup")
    return results


def _rising_feed_url(subreddit: str) -> str:
    return f"https://www.reddit.com/svc/shreddit/community-more-posts/rising/?name={subreddit}"


async def _monid_free_fetch(client: httpx.AsyncClient, urls: list[str]) -> dict:
    """Run TinyFish's free fetch through Monid; return its ``{results, errors}``.

    Sync providers answer the POST; async ones answer 202 with a run id to
    poll (monid.ai/docs/api/run). Any refusal (402 balance, BLOCKED,
    provider error) raises, so the caller counts every URL as failed.
    """
    headers = {"Authorization": f"Bearer {os.environ['MONID_API_KEY']}"}
    response = await client.post(
        f"{MONID_API_URL}/v1/run",
        headers=headers,
        json={
            "provider": "tinyfish",
            "endpoint": "/free/fetch",
            "input": {
                "urls": urls,
                "format": "html",
                "ttl": 0,
                "include_selectors": ["shreddit-post"],
            },
        },
    )
    if response.status_code not in (200, 202):
        raise RuntimeError(f"Monid run refused: HTTP {response.status_code} {response.text[:200]}")
    run = response.json()
    if run.get("status") != "COMPLETED" and not run.get("runId"):
        raise RuntimeError(f"Monid run {run.get('status')} without a run id")
    deadline = asyncio.get_running_loop().time() + MONID_RUN_DEADLINE_SECONDS
    while run.get("status") in ("READY", "RUNNING", "STOPPING"):
        if asyncio.get_running_loop().time() > deadline:
            raise RuntimeError(f"Monid run {run.get('runId')} timed out")
        await asyncio.sleep(MONID_POLL_INTERVAL_SECONDS)
        poll = await client.get(f"{MONID_API_URL}/v1/runs/{run['runId']}", headers=headers)
        if poll.status_code != 200:
            raise RuntimeError(f"Monid run poll failed: HTTP {poll.status_code}")
        run = poll.json()
    provider_status = (run.get("providerResponse") or {}).get("httpStatus") or 200
    if run.get("status") != "COMPLETED" or provider_status >= 400:
        raise RuntimeError(
            f"Monid run {run.get('runId')} ended {run.get('status')} "
            f"(provider HTTP {provider_status}): {run.get('reason') or ''}".strip()
        )
    output = run.get("output") or {}
    return {"results": output.get("results") or [], "errors": output.get("errors") or []}


async def _fetch_all_subreddits(
    client: httpx.AsyncClient,
) -> tuple[list[dict], list[str], set[str]]:
    """Parsed posts, one failure message per failed subreddit, and the subreddits read."""
    urls = {_rising_feed_url(name): name for name in SUBREDDITS}
    pending = list(urls)
    posts: list[dict] = []
    failures: list[str] = []
    read: set[str] = set()
    for i in range(0, len(pending), MONID_FETCH_BATCH):
        batch = pending[i : i + MONID_FETCH_BATCH]
        try:
            output = await _monid_free_fetch(client, batch)
        except (httpx.HTTPError, RuntimeError, ValueError) as error:
            failures.extend(f"r/{urls[u]}: {error}" for u in batch)
            continue
        for error in output["errors"]:
            name = urls.get(error.get("url"), error.get("url"))
            failures.append(f"r/{name}: {error.get('error')} {error.get('status') or ''}".strip())
        for result in output["results"]:
            name = urls.get(result.get("url"), result.get("url"))
            try:
                posts.extend(parse_rising_feed(result.get("text") or ""))
            except ValueError as error:
                failures.append(f"r/{name}: {error}")
                continue
            read.add(name)
    return posts, failures, read


async def fetch_reddit_candidates(
    max_results: int = 10, db=None, client: httpx.AsyncClient | None = None
) -> list[dict]:
    """Discover trending Reddit AI posts from each subreddit's rising feed.

    Fetches through Monid's free TinyFish fetch ($0, 1,000 URLs/day), two
    calls of at most 10 feeds. Skips Reddit threads that were posted
    (published as HypeRadar posts) in the last ``COOLDOWN_DAYS`` days. When
    *db* is not provided, connects via ``_get_db()``.
    """
    if not os.environ.get("MONID_API_KEY", "").strip():
        raise RuntimeError("MONID_API_KEY not set — required for the Reddit source")

    if db is None:
        db = _get_db()

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=MONID_HTTP_TIMEOUT_SECONDS)
    try:
        all_posts, failures, read = await _fetch_all_subreddits(client)
    finally:
        if owns_client:
            await client.aclose()
    for failure in failures:
        logging.warning("reddit_source fetch failed: %s", failure)

    # Every subreddit failing is a dead source (an exhausted allowance, a
    # changed markup), not a quiet day: fail the run so the refresh alert fires.
    if not read:
        raise RuntimeError(
            f"Reddit source down: all {len(SUBREDDITS)} subreddit fetches failed; "
            f"last error: {failures[-1] if failures else 'Monid returned no page'}"
        )

    # Deduplicate by URL
    seen = set()
    unique = []
    for c in all_posts:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)

    # Delta-based discovery: compute the upvote delta since the last run's
    # snapshot. Items that gained the most since last run surface higher — this
    # is what makes day-to-day results actually change. Save the current
    # snapshot for the next run's delta.
    for c in unique:
        prior = await _last_reddit_snapshot(db, c["url"])
        prior_upvotes = prior.get("upvotes", 0) if prior else 0
        c["upvotes_delta"] = max(0, c["num_upvotes"] - prior_upvotes)
        await _save_reddit_snapshot(
            db,
            c["url"],
            c["num_upvotes"],
            c["num_comments"],
            title=c["title"],
            description=c["description"],
            subreddit=c.get("subreddit", ""),
        )

    # Attach heat scores to all unique candidates (before the gate).
    attach_reddit_heat(unique)

    # Publish gate: cooldown + threshold + noise floor + outperform.
    # Replaces the old simple cooldown check with the full gate.
    gated: list[dict] = []
    for c in unique:
        heat_result = {
            "heat_score": c["heat_score"],
            "outperform_ratio": c["outperform_ratio"],
            "baseline_confidence": c["baseline_confidence"],
        }
        item = {
            "upvotes": c["num_upvotes"],
            "comments": c["num_comments"],
            "age_hours": c["age_hours"],
        }
        last_days = await _last_posted_days(db, c["url"])
        if should_publish_heat("reddit", heat_result, item, last_days):
            gated.append(c)

    # Sort by heat_score (highest first), with upvotes_delta as a tiebreaker.
    gated.sort(key=lambda c: (c["heat_score"], c.get("upvotes_delta", 0)), reverse=True)
    print(
        f"@reddit-pulse source: candidates_returned={len(unique)} "
        f"gate_passed={len(gated)}",
        flush=True,
    )
    return gated[:max_results]


def attach_reddit_heat(candidates: list[dict]) -> list[dict]:
    """Attach a shared heat_score to each candidate (beside existing fields).

    Expand phase: adds ``heat_score``, ``outperform_ratio``, and
    ``baseline_confidence`` without removing the existing visibility_score /
    engagement_velocity fields. The baseline for each post is the other recent
    posts from the same subreddit (their ``num_upvotes`` + age), passed to the
    shared scorer. The scored post is excluded from its own baseline.
    """
    if not candidates:
        return candidates
    by_subreddit: dict[str, list[dict]] = {}
    for c in candidates:
        by_subreddit.setdefault(c.get("subreddit", ""), []).append(c)
    for c in candidates:
        subreddit = c.get("subreddit", "")
        baseline = [
            {
                "upvotes": other.get("num_upvotes", 0),
                "age_hours": other.get("age_hours", 0.0),
            }
            for other in by_subreddit.get(subreddit, [])
            if other is not c
        ]
        item = {
            "upvotes": c.get("num_upvotes", 0),
            "comments": c.get("num_comments", 0),
            "age_hours": c.get("age_hours", 0.0),
        }
        result = compute_heat_score("reddit", item, baseline, prior_posts=0)
        c["heat_score"] = result["heat_score"]
        c["outperform_ratio"] = result["outperform_ratio"]
        c["baseline_confidence"] = result["baseline_confidence"]
    return candidates
