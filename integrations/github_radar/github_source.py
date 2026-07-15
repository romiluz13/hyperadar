"""GitHub trending source — uses the Search API as the "aggregator".

GitHub has no official trending endpoint (anonymous scraping is blocked — see
docs/reference/source-constraints-and-costs.md). The Search API is the official
proxy: recently-created, high-star repos sorted by stars. Authenticated via
GITHUB_TOKEN (5k req/h).
"""

import contextlib
import os
from datetime import datetime, timedelta, timezone

import httpx

_token = os.environ["GITHUB_TOKEN"]
_headers = {"Authorization": f"token {_token}", "Accept": "application/vnd.github+json"}


async def fetch_trending_candidates(max_results: int = 10) -> list[dict]:
    """Fetch trending candidate repos: created in the last 30 days, sorted by stars.

    Returns normalized candidate dicts with enough data to score + write.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    # stars:>50 to filter noise; sort by stars desc. Language-agnostic for breadth.
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
                "created_at": it.get("created_at"),
                "pushed_at": it.get("pushed_at"),
                "language": it.get("language"),
                "owner": it["owner"]["login"],
                "repo": it["name"],
            }
        )
    return candidates


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
