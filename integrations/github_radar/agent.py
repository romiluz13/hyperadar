"""@github-radar: today's trending AI and dev-tools repos, picked by plain code.

One run reads the trending pages (or REST search when they fail), looks each
repo up, keeps the AI/dev-tools ones, reads their star history, drops likely
farmed ones, orders the rest by hype score and writes a post for each one
outside the republish cooldown, up to ``MAX_POSTS_PER_RUN``. No LLM picks a
repo or sets a verdict (ADR 0007).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx

from _shared import mongo
from _shared.agent_catalog import agent_identity
from _shared.engagement import (
    _ENGAGEMENT_WINDOW_DAYS,
    _HN_ALGOLIA_SEARCH_URL,
    _story_matches,
)
from _shared.hype_score import HYPE_NEW_REPO_AGE, hype_score
from _shared.momentum import _REPUBLISH_COOLDOWN_DAYS
from _shared.star_history import check_trending_agreement, fetch_stars_this_week_batch
from _shared.write_post import write_post
import scope
from farm_check import farm_reason
from github_source import Repo, enrich, readme_excerpt, search_fallback
from trending_source import (
    TrendingUnavailable,
    fetch_trending,
    write_trending_snapshots,
)

AGENT_HANDLE = "@github-radar"
_IDENTITY = agent_identity(AGENT_HANDLE)
AGENT_NAME = _IDENTITY["name"]
AGENT_BIO = _IDENTITY["bio"]
SOURCE_TYPE = _IDENTITY["source_type"]

# write_post takes 0-100 scores. Hype reaches about 4.3 at the very top, so
# ×25 keeps nearly every real repo below the cap.
HYPE_TO_POST_SCORE = 25
# The digest shows 3 repos; every post costs an embedding, a Port sync and
# Atlas Free storage, and an uncapped run once wrote 51.
MAX_POSTS_PER_RUN = 10


@dataclass(frozen=True)
class ScoredRepo:
    repo: Repo
    stars_this_week: int
    stars_today: int
    hn_points: int | None
    hype: float


def _log(message: str) -> None:
    # print, not logging: the agents configure no log handler, so INFO never
    # reaches the CI log.
    print(f"{AGENT_HANDLE} {message}", flush=True)


def post_score(hype: float) -> int:
    """The post's ``rank_score`` and ``momentumScore``: 25 × hype, capped at 100."""
    return round(min(100, HYPE_TO_POST_SCORE * hype))


def verdict_for(created_at: datetime, now: datetime) -> str:
    return "emerging" if now - created_at < HYPE_NEW_REPO_AGE else "hype looks real"


async def _in_scope(client: httpx.AsyncClient, repo: Repo) -> bool:
    reason = scope.drop_reason(repo.slug, repo.description, repo.topics)
    if reason == scope.OFF_SCOPE:
        readme = await readme_excerpt(client, repo.slug)
        reason = scope.drop_reason(repo.slug, repo.description, repo.topics, readme)
    if reason:
        _log(f"dropped {repo.slug}: {reason}")
    return reason is None


async def _hn_points_by_day(
    client: httpx.AsyncClient, slug: str, now: datetime
) -> dict[date, int]:
    """The best matching HN story's points per UTC day, over the digest's HN window.

    {} when HN cannot say: HN raises a score, it never fails the run.
    """
    since = now - timedelta(days=_ENGAGEMENT_WINDOW_DAYS)
    points: dict[date, int] = {}
    try:
        response = await client.get(
            _HN_ALGOLIA_SEARCH_URL,
            params={
                "query": f'"{slug}"',
                "tags": "story",
                "numericFilters": f"created_at_i>{int(since.timestamp())}",
                "hitsPerPage": 20,
            },
        )
        response.raise_for_status()
        for hit in response.json().get("hits", []):
            if _story_matches(hit, slug):
                day = datetime.fromtimestamp(
                    hit["created_at_i"], tz=timezone.utc
                ).date()
                points[day] = max(points.get(day, 0), hit.get("points") or 0)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
        _log(f"HN lookup failed for {slug}: {e!r}")
        return {}
    return points


async def _score(
    client: httpx.AsyncClient,
    repos: list[Repo],
    printed_weekly: dict[str, int],
    now: datetime,
) -> list[ScoredRepo]:
    weeks, _failures = await fetch_stars_this_week_batch(
        [repo.slug for repo in repos], now=now, client=client
    )
    scored: list[ScoredRepo] = []
    for repo in repos:
        week = weeks.get(repo.slug)
        if week is None:
            continue
        if repo.slug in printed_weekly:
            check_trending_agreement(
                repo.slug, printed_weekly[repo.slug], week["starsThisWeek"]
            )
        hn_by_day = await _hn_points_by_day(client, repo.slug, now)
        farmed = farm_reason(
            forks=repo.forks,
            stars_this_week=week["starsThisWeek"],
            days=week["days"],
            created_at=repo.created_at,
            now=now,
            hn_points_by_day=hn_by_day,
        )
        if farmed:
            _log(f"excluded {repo.slug} as likely farmed: {farmed}")
            continue
        hn_points = max(hn_by_day.values(), default=None)
        scored.append(
            ScoredRepo(
                repo=repo,
                stars_this_week=week["starsThisWeek"],
                stars_today=week["starsToday"],
                hn_points=hn_points,
                hype=hype_score(
                    stars_this_week=week["starsThisWeek"],
                    stars_today=week["starsToday"],
                    total_stars=repo.stars,
                    hn_points=hn_points,
                    created_at=repo.created_at,
                    now=now,
                ),
            )
        )
    return scored


async def publish(item: ScoredRepo, now: datetime) -> str:
    """Write one repo's post; every number in it comes from the evidence."""
    repo = item.repo
    score = post_score(item.hype)
    verdict = verdict_for(repo.created_at, now)
    blurb = (
        f"{item.stars_this_week:,} GitHub stars in the last 7 days "
        f"({item.stars_today:,} on the latest full day); {repo.stars:,} in all."
    )
    if item.hn_points:
        blurb += f" {item.hn_points:,} points on Hacker News."
    project = {
        "url": repo.url,
        "title": repo.slug,
        "kind": "repo",
        "description": repo.description,
        "topics": repo.topics,
        "momentumScore": score,
        "hypeVerdict": verdict,
    }
    signal = {
        "source": "github",
        "metric": "github_stars",
        "value": repo.stars,
        "delta": item.stars_this_week,
        "evidenceUrl": repo.url,
        "evidenceLabel": "Open GitHub repository",
        "summary": (
            f"starsThisWeek={item.stars_this_week}; starsToday={item.stars_today}; "
            f"stars={repo.stars}; hnPoints={item.hn_points or 0}; hype={item.hype:.3f}"
        ),
    }
    return await write_post(
        AGENT_HANDLE,
        AGENT_NAME,
        AGENT_BIO,
        SOURCE_TYPE,
        project,
        blurb,
        verdict,
        signal,
        score,
    )


async def scan(db, now: datetime | None = None) -> int:
    """Run today's selection and write its posts. Returns the repos considered.

    Trending failing its loud-failure rule switches discovery to REST search
    (ADR 0007). A repo posted by any agent inside the republish cooldown is
    skipped, and at most ``MAX_POSTS_PER_RUN`` of the rest, highest hype first,
    are written. Writes ``trending_snapshots`` and posts, never ``signals``
    snapshot rows, which hidden-gems alone needs.
    """
    now = now or datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        try:
            rows = await fetch_trending()
        except TrendingUnavailable as e:
            _log(f"trending unavailable, falling back to REST search: {e}")
            repos = await search_fallback(client, now)
            printed_weekly: dict[str, int] = {}
        else:
            await write_trending_snapshots(db, rows, now)
            repos = await enrich(client, list(dict.fromkeys(row.slug for row in rows)))
            printed_weekly = {
                row.slug: row.window_stars for row in rows if row.window == "weekly"
            }
        kept = [repo for repo in repos if await _in_scope(client, repo)]
        scored = await _score(client, kept, printed_weekly, now)

    published = 0
    for item in sorted(scored, key=lambda s: s.hype, reverse=True):
        if published == MAX_POSTS_PER_RUN:
            break
        days = await mongo.get_last_published_days(db, item.repo.url)
        if days < _REPUBLISH_COOLDOWN_DAYS:
            _log(
                f"skipped {item.repo.slug}: posted {days} day(s) ago, inside the "
                f"{_REPUBLISH_COOLDOWN_DAYS}-day republish cooldown"
            )
            continue
        await publish(item, now)
        published += 1
    _log(
        f"considered={len(repos)} in_scope={len(kept)} scored={len(scored)} "
        f"published={published}"
    )
    return len(repos)
