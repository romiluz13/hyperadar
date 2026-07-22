"""GitHub trending source — OSSInsight velocity API + Search API fallback.

OSSInsight ingests the GH Archive (10B+ events) and counts WatchEvent (star)
events in fixed time windows, giving deterministic star *velocity* (stars
gained today), not the lifetime-total sorting the GitHub Search API is limited
to. The Search API remains a fallback when OSSInsight is unreachable.
"""

import contextlib
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from _shared.momentum import (
    _acceleration,
    _velocity,
    compute_momentum_score,
    passes_fake_star_filter,
)
from github_radar.tracker import track_daily_snapshots

_token = os.environ["GITHUB_TOKEN"]
_headers = {"Authorization": f"token {_token}", "Accept": "application/vnd.github+json"}


def _stars_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def _fetch_ossinsight_trending(max_results: int) -> list[dict]:
    """Fetch repos trending by star velocity via the OSSInsight public API.

    Returns star *deltas* (stars gained in the period), not lifetime totals.
    Post-filters for AI relevance via repo topics on the GitHub API.
    """
    candidates: list[dict] = []
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            "https://api.ossinsight.io/v1/trends/repos/",
            params={"period": "past_24_hours"},
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        body = r.json()
        rows = body.get("data", {}).get("rows", body.get("data", []))
        if not isinstance(rows, list):
            return []
        for row in rows[: max_results * 5]:  # over-fetch 5x, then AI-filter
            repo_name = row.get("repo_name") or ""
            if not repo_name or "/" not in repo_name:
                continue
            owner, repo = repo_name.split("/", 1)
            candidates.append(
                {
                    "url": f"https://github.com/{repo_name}",
                    "title": repo_name,
                    "kind": "repo",
                    "description": row.get("description") or "",
                    "topics": [],  # resolved below via GitHub API
                    "stars": _stars_int(row.get("stars")),  # velocity delta
                    "created_at": None,
                    "pushed_at": None,
                    "language": row.get("primary_language"),
                    "owner": owner,
                    "repo": repo,
                    "trending_stars_24h": _stars_int(row.get("stars")),
                }
            )
    if not candidates:
        return []
    # Enrich with GitHub topics + metadata, filter for AI relevance.
    # Broader than topics alone: also check the description for AI keywords,
    # since many AI repos (e.g. stablyai/orca) lack ai as a GitHub topic.
    ai_topic_keywords = {
        "ai",
        "llm",
        "agent",
        "gpt",
        "openai",
        "machine-learning",
        "ml",
        "chatgpt",
        "claude",
        "anthropic",
        "rag",
        "transformer",
        "deep-learning",
        "neural-network",
        "copilot",
        "agentic",
    }
    ai_desc_keywords = [
        "ai",
        "agent",
        "llm",
        "gpt",
        "model",
        "coding",
        "code agent",
        "dev tool",
        "automation",
        "copilot",
        "prompt",
        "inference",
        "fine-tune",
        "embedding",
        "vector",
        "chatbot",
        "assistant",
    ]
    ai_candidates: list[dict] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for c in candidates:
            if len(ai_candidates) >= max_results:
                break
            try:
                r = await client.get(
                    f"https://api.github.com/repos/{c['owner']}/{c['repo']}",
                    headers=_headers,
                )
                if r.status_code != 200:
                    continue
                data = r.json()
                topics = data.get("topics") or []
                desc = (data.get("description") or c["description"] or "").lower()
                topic_match = {t.lower() for t in topics} & ai_topic_keywords
                desc_match = any(kw in desc for kw in ai_desc_keywords)
                if not topic_match and not desc_match:
                    continue  # not AI-related by topic or description
                c["topics"] = topics
                c["stars"] = data.get("stargazers_count", c["stars"])
                # Skip repos with 0 stars (data unavailable or brand-new).
                if c["stars"] == 0:
                    continue
                c["created_at"] = data.get("created_at")
                c["pushed_at"] = data.get("pushed_at")
                c["description"] = data.get("description") or c["description"]
                c["forks"] = data.get("forks_count")
                ai_candidates.append(c)
            except Exception:
                continue
    return ai_candidates


def _passes_fake_star_filter(stars: int, forks) -> bool:
    """Pass through when fork data is missing; otherwise apply the fake-star filter."""
    if forks is None:
        return True
    return passes_fake_star_filter(stars, forks)


async def fetch_trending_candidates(max_results: int = 10) -> list[dict]:
    """Fetch trending candidate repos via OSSInsight velocity, Search API fallback.

    OSSInsight returns repos by stars-gained-today (velocity); the Search API
    fallback returns recently-created high-star repos (lifetime total). Both
    paths are filtered for AI relevance.
    """
    try:
        candidates = await _fetch_ossinsight_trending(max_results)
        if candidates:
            return [
                c
                for c in candidates
                if _passes_fake_star_filter(c.get("stars", 0), c.get("forks"))
            ]
        logging.info("OSSInsight returned no AI repos; falling back to Search API")
    except Exception as e:
        logging.warning(
            "OSSInsight trending fetch failed, falling back to Search API: %s", e
        )

    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    params = {
        "q": f"created:>{since} stars:>200 topic:ai",
        "sort": "stars",
        "order": "desc",
        "per_page": max_results,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            "https://api.github.com/search/repositories",
            params=params,
            headers=_headers,
        )
        r.raise_for_status()
        items = r.json().get("items", [])

    candidates = []
    for it in items:
        candidates.append(
            {
                "url": it["html_url"],
                "title": it["full_name"],
                "kind": "repo",
                "description": it.get("description") or "",
                "topics": it.get("topics") or [],
                "stars": it.get("stargazers_count", 0),
                "forks": it.get("forks_count", 0),
                "created_at": it.get("created_at"),
                "pushed_at": it.get("pushed_at"),
                "language": it.get("language"),
                "owner": it["owner"]["login"],
                "repo": it["name"],
            }
        )
    return [
        c
        for c in candidates
        if _passes_fake_star_filter(c.get("stars", 0), c.get("forks"))
    ]


async def get_repo_details(owner: str, repo: str) -> dict:
    """Full repo metadata (1 req, cheap)."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}", headers=_headers
        )
        r.raise_for_status()
        return r.json()


def _as_utc(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _has_sustained_six_week_growth(
    history: list[dict], current_value: float, current_at: datetime
) -> bool:
    observations = sorted(
        [
            (captured_at, float(signal.get("value", 0)))
            for signal in history
            if (captured_at := _as_utc(signal.get("capturedAt"))) is not None
        ]
        + [(current_at, float(current_value))],
        key=lambda observation: observation[0],
    )
    if len(observations) < 6:
        return False
    if observations[-1][0] - observations[0][0] < timedelta(days=35):
        return False
    return observations[-1][1] > observations[0][1] and all(
        current[1] >= previous[1]
        for previous, current in zip(observations, observations[1:])
    )


def compute_momentum(candidate: dict, history: list[dict], prior_posts: int) -> dict:
    """Deterministic momentum score (0-100) per docs/specs design.

    Legacy scoring path. Prefer ``fetch_trending_candidates_with_momentum``
    which uses the shared ``compute_momentum_score`` from ``_shared.momentum``.

    Velocity (40%): lifetime-average stars per week since creation.
    Sustainedness (25%): six observations spanning at least five weeks, net-positive
    and non-decreasing through the current candidate value.
    Novelty (15%): fewer prior posts => more novel.
    Multi-source (20%): N/A in @github-radar alone (set in T5 cross-agent) -> base 50.

    Returns explicitly labeled evidence fields so lifetime averages cannot be
    presented as recent star velocity.
    """
    now = datetime.now(timezone.utc)
    created = _as_utc(candidate.get("created_at"))
    age_days = 30.0  # default if missing
    if created:
        age_days = max((now - created).total_seconds() / 86_400, 1)
    stars = candidate.get("stars", 0)
    average_stars_per_week = stars / (age_days / 7)

    velocity = min(average_stars_per_week / 50, 1.0) * 100
    sustained_six_week_growth = _has_sustained_six_week_growth(history, stars, now)
    sustained = 60.0 if sustained_six_week_growth else 30.0
    novel = max(100 - prior_posts * 25, 0)
    multisource = 50.0  # base; boosted in T5 when cross-agent confirmation exists

    score = 0.40 * velocity + 0.25 * sustained + 0.20 * multisource + 0.15 * novel
    return {
        "momentumScore": round(min(score, 100), 1),
        "avgStarsPerWeekSinceCreation": round(average_stars_per_week, 1),
        "sustainedSixWeekGrowth": sustained_six_week_growth,
        "novel": prior_posts == 0,
    }


# ─── Shared Momentum Score path (compute_momentum_score from _shared.momentum) ───

_MIN_HISTORY_DAYS = 7


async def fetch_trending_candidates_with_momentum(db) -> list[dict]:
    """Fetch trending AI repos using the shared Momentum Score.

    1. Store today's snapshots via ``track_daily_snapshots``.
    2. Query the signals time-series for repos with >=7 days of history.
    3. For each, compute the shared ``compute_momentum_score``.
    4. Apply the fake-star filter.
    5. Return candidates with momentumScore, velocity, acceleration.
    """
    await track_daily_snapshots(db)

    # Find all projectIds with >=7 days of pre-publication snapshots.
    pipeline = [
        {"$match": {"postId": ""}},
        {
            "$group": {
                "_id": "$projectId",
                "count": {"$sum": 1},
            }
        },
        {"$match": {"count": {"$gte": _MIN_HISTORY_DAYS}}},
    ]
    candidates_with_history = await (await db.signals.aggregate(pipeline)).to_list(
        length=None
    )

    results: list[dict] = []
    for doc in candidates_with_history:
        project_url = doc["_id"]

        # Fetch the full history sorted by capturedAt ascending.
        cursor = db.signals.find(
            {"projectId": project_url, "postId": ""},
            {"capturedAt": 1, "github_stars": 1, "github_forks": 1, "_id": 0},
        ).sort("capturedAt", 1)
        history = await cursor.to_list(length=None)

        if len(history) < _MIN_HISTORY_DAYS:
            continue

        score = compute_momentum_score(history)
        velocity = _velocity(history, 7)
        acceleration = _acceleration(history)
        stars = history[-1].get("github_stars", 0)
        forks = history[-1].get("github_forks", 0)

        if not passes_fake_star_filter(stars, forks):
            continue

        results.append(
            {
                "url": project_url,
                "title": project_url.rsplit("/", 1)[-1],
                "kind": "repo",
                "description": "",
                "topics": ["ai", "github-radar"],
                "discovery_source": "momentum",
                "evidence_url": project_url,
                "stars": stars,
                "github_stars": stars,
                "github_forks": forks,
                "momentumScore": score,
                "velocity": velocity,
                "acceleration": acceleration,
            }
        )
    return results
