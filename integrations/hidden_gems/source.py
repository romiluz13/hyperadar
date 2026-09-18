"""Hidden gems source — HN candidates + breakout prediction pipeline.

@hidden-gems finds things BEFORE they blow up: HN Show HN posts linking to
novel repos, arXiv papers with fresh code repos, and repos identified by the
momentum-score breakout gate.
"""

import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx

from _shared.engagement import engagement_boost, fetch_hn_engagement
from _shared.momentum import (
    _acceleration,
    _engagement_depth,
    _is_monotonic_growth,
    _velocity,
    compute_momentum_score,
    passes_fake_star_filter,
    publish_score_threshold,
    should_publish_hidden_gem,
)
from _shared.mongo import get_last_published_days
from hidden_gems.tracker import track_daily_snapshots

_github_token = os.environ.get("GITHUB_TOKEN", "")
_headers = (
    {"Authorization": f"token {_github_token}", "Accept": "application/vnd.github+json"}
    if _github_token
    else {}
)

_MIN_HISTORY_DAYS = 7


def normalize_hn_story(story: dict, story_id: int) -> dict:
    """Keep Hacker News evidence labeled as Hacker News evidence."""
    url = story.get("url", "")
    title = story.get("title", "")
    return {
        "url": url or f"https://news.ycombinator.com/item?id={story_id}",
        "title": title[:200],
        "kind": "repo" if "github.com" in url else "thread",
        "description": title,
        "topics": ["hn", "hidden-gem", "ai"],
        "discovery_source": "hacker_news",
        "evidence_url": f"https://news.ycombinator.com/item?id={story_id}",
        "hn_points": story.get("score", 0),
        "hn_comments": story.get("descendants", 0),
    }


async def fetch_hn_candidates(max_results: int = 5) -> list[dict]:
    """Fetch Show HN posts with traction via the Algolia HN API.

    Algolia returns full JSON objects with searchable tags (show_hn) and
    numeric filters (points), unlike the Firebase API which only returns IDs.
    Show HN posts surface GitHub repos 24-48h before they trend.
    """
    candidates: list[dict] = []
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            "https://hn.algolia.com/api/v1/search",
            params={
                "tags": "show_hn",
                "numericFilters": "points>50",
                "hitsPerPage": 20,
            },
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
    for hit in hits:
        if len(candidates) >= max_results:
            break
        url = hit.get("url") or ""
        title = hit.get("title") or ""
        story_id = hit.get("objectID") or ""
        if not title:
            continue
        candidates.append(
            {
                "url": url or f"https://news.ycombinator.com/item?id={story_id}",
                "title": title[:200],
                "kind": "repo" if "github.com" in url else "thread",
                "description": title,
                "topics": ["hn", "hidden-gem", "ai"],
                "discovery_source": "hacker_news",
                "evidence_url": f"https://news.ycombinator.com/item?id={story_id}",
                "hn_points": hit.get("points", 0),
                "hn_comments": hit.get("num_comments", 0),
            }
        )
    return candidates


async def fetch_low_star_github_candidates(max_results: int = 15) -> list[dict]:
    """Recently created GitHub repos with 10–200 stars (true hidden gems).

    Searches across ai, llm, and agent topics to find niche tools.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    all_candidates = []
    async with httpx.AsyncClient(timeout=30) as client:
        for topic in ["ai", "llm", "agent"]:
            if len(all_candidates) >= max_results:
                break
            params = {
                "q": f"created:>{since} stars:10..200 topic:{topic} sort:updated",
                "sort": "updated",
                "order": "desc",
                "per_page": min(max_results - len(all_candidates), 30),
            }
            try:
                r = await client.get(
                    "https://api.github.com/search/repositories",
                    params=params,
                    headers=_headers,
                )
                r.raise_for_status()
                items = r.json().get("items", [])
            except Exception as e:
                logging.warning("GitHub search failed for topic '%s': %s", topic, e)
                continue
            for it in items:
                stars = it.get("stargazers_count", 0)
                if stars == 0:
                    continue
                all_candidates.append(
                    {
                        "url": it["html_url"],
                        "title": it["full_name"],
                        "kind": "repo",
                        "description": it.get("description") or "",
                        "topics": it.get("topics") or [],
                        "discovery_source": "github",
                        "evidence_url": it["html_url"],
                        "github_stars": stars,
                        "created_at": it.get("created_at"),
                        "owner": it["owner"]["login"],
                        "repo": it["name"],
                    }
                )
    seen = set()
    unique = []
    for c in all_candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)
    return unique[:max_results]


async def fetch_breakout_candidates(db) -> list[dict]:
    """Find repos that pass the breakout prediction gate.

    1. Store today's snapshots via track_daily_snapshots.
    2. Query the signals time-series for repos with >=7 days of history.
    3. Score every repo in the pool, then scale the publish threshold to the
       pool's 80th percentile (``publish_score_threshold``).
    4. Apply the publishing gate (with cross-agent cooldown) against that
       threshold and return only repos that pass, with score and velocity.
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

    # First pass: score every repo in the pool so the publish threshold can
    # scale to today's distribution instead of a hardcoded cutoff that only
    # already-trending repos could clear.
    scored: list[dict] = []
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

        prior_count = await db.posts.count_documents({"project.url": project_url})
        score = compute_momentum_score(history, prior_post_count=prior_count)
        velocity = _velocity(history, 7)
        acceleration = _acceleration(history)
        stars = history[-1].get("github_stars", 0)
        forks = history[-1].get("github_forks", 0)
        fork_star_ratio = _engagement_depth(history)

        if not passes_fake_star_filter(stars, forks):
            continue

        scored.append(
            {
                "project_url": project_url,
                "score": score,
                "velocity": velocity,
                "acceleration": acceleration,
                "stars": stars,
                "forks": forks,
                "fork_star_ratio": fork_star_ratio,
                "is_monotonic": _is_monotonic_growth(history),
            }
        )

    threshold = publish_score_threshold([row["score"] for row in scored])

    # Second pass: behavioral gates + cross-agent cooldown, against the
    # pool-scaled threshold.
    results: list[dict] = []
    for row in scored:
        project_url = row["project_url"]
        last_pub_days = await get_last_published_days(db, project_url)
        if not should_publish_hidden_gem(
            row["score"],
            row["velocity"],
            row["acceleration"],
            row["fork_star_ratio"],
            last_pub_days,
            row["is_monotonic"],
            score_threshold=threshold,
        ):
            continue

        # Post-gate engagement weighting: HN attention on the same repo
        # raises the published Momentum Score (never the gate decision, so
        # the pool-scaled threshold stays GitHub-native).
        published_score = row["score"]
        engagement = await fetch_hn_engagement(project_url)
        if engagement is not None:
            published_score = min(
                100, published_score + engagement_boost(
                    engagement["points"], engagement["comments"]
                )
            )

        candidate = {
            "url": project_url,
            "title": project_url.rsplit("/", 1)[-1],
            "kind": "repo",
            "description": "",
            "topics": ["ai", "hidden-gem"],
            "discovery_source": "breakout",
            "evidence_url": project_url,
            "github_stars": row["stars"],
            "github_forks": row["forks"],
            "momentumScore": published_score,
            "velocity": row["velocity"],
            "acceleration": row["acceleration"],
        }
        if engagement is not None:
            candidate["hn_points"] = engagement["points"]
            candidate["hn_comments"] = engagement["comments"]
            candidate["hn_story_url"] = engagement["story_url"]
            candidate["hn_top_comment"] = engagement["top_comment"]
        results.append(candidate)
    return results


async def fetch_hidden_gems(max_results: int = 8) -> list[dict]:
    """Combine HN + low-star GitHub to find hidden gems before they blow up.

    Kept for backward compatibility — prefer fetch_breakout_candidates.
    """
    hn = await fetch_hn_candidates(max_results=10)
    gh = await fetch_low_star_github_candidates(max_results=15)
    return (hn + gh)[:max_results]


# ─── arXiv discovery: papers with code repos, days before trending ───

_ARXIV_API_URL = "https://export.arxiv.org/api/query"
_ARXIV_CATEGORIES = "cat:cs.AI OR cat:cs.CL OR cat:cs.LG"
# A paper older than this is research coverage, not a fresh discovery.
_ARXIV_MAX_AGE_DAYS = 14
# Traction floor: a paper-linked repo with <10 stars has no observable
# community signal yet; the tracker's search bands will pick it up once it
# accrues history instead.
_ARXIV_MIN_STARS = 10
_ARXIV_FETCH_LIMIT = 50
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_GITHUB_LINK_RE = re.compile(
    r"github\.com/([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)", re.IGNORECASE
)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_arxiv_entries(xml_text: str) -> list[dict]:
    """Parse an arXiv Atom feed into entry dicts (title, summary, published, url).

    Pure function over the raw XML so the parser is testable without network.
    Entries missing a title, summary, or date are dropped.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    entries = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        title = _clean_text(entry.findtext(f"{_ATOM_NS}title") or "")
        summary = _clean_text(entry.findtext(f"{_ATOM_NS}summary") or "")
        published = entry.findtext(f"{_ATOM_NS}published") or ""
        url = ""
        for link in entry.findall(f"{_ATOM_NS}link"):
            if (link.get("type") or "") == "application/pdf":
                continue
            url = link.get("href") or url
        if not title or not summary or not published:
            continue
        entries.append(
            {"title": title, "summary": summary, "published": published, "url": url}
        )
    return entries


def _published_within_days(published: str, now: datetime, max_days: int) -> bool:
    try:
        parsed = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except (ValueError, TypeError):  # TypeError: a naive date breaks now - parsed
        return False
    return now - parsed <= timedelta(days=max_days)


def _extract_repo_url(text: str) -> str | None:
    match = _GITHUB_LINK_RE.search(text or "")
    if not match:
        return None
    slug = match.group(1).rstrip(".")
    if slug.lower().endswith(".git"):  # clone URLs name the repo, not a page
        slug = slug[: -len(".git")]
    return f"https://github.com/{slug}"


async def fetch_arxiv_candidates(
    max_results: int = 8, client: httpx.AsyncClient | None = None
) -> list[dict]:
    """Discover fresh arXiv papers (cs.AI/CL/LG) that link to a GitHub repo.

    Papers surface research-linked repos days before any trending list; each
    repo is enriched via the GitHub API and must clear the traction floor
    (>=10 stars) to become a candidate. One feed request + one repo request
    per unique repo — arXiv asks clients to stay near one request per 3s,
    which a single daily run satisfies.
    """
    now = datetime.now(timezone.utc)
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30)
    try:
        r = await client.get(
            _ARXIV_API_URL,
            params={
                "search_query": _ARXIV_CATEGORIES,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": _ARXIV_FETCH_LIMIT,
            },
        )
        r.raise_for_status()
        entries = parse_arxiv_entries(r.text)

        candidates: list[dict] = []
        seen_repos: set[str] = set()
        for entry in entries:
            if not _published_within_days(entry["published"], now, _ARXIV_MAX_AGE_DAYS):
                continue
            repo_url = _extract_repo_url(entry["summary"])
            if not repo_url or repo_url in seen_repos:
                continue
            seen_repos.add(repo_url)

            owner_repo = repo_url.removeprefix("https://github.com/")
            try:
                repo_r = await client.get(
                    f"https://api.github.com/repos/{owner_repo}", headers=_headers
                )
                if repo_r.status_code != 200:
                    continue
                data = repo_r.json()
            except Exception as e:
                logging.warning("GitHub enrich failed for %s: %s", repo_url, e)
                continue

            stars = data.get("stargazers_count", 0)
            if stars < _ARXIV_MIN_STARS:
                continue

            candidates.append(
                {
                    "url": repo_url,
                    "title": data.get("full_name") or owner_repo,
                    "kind": "repo",
                    "description": data.get("description") or entry["title"],
                    "topics": ["arxiv", "hidden-gem", "ai"],
                    "discovery_source": "arxiv",
                    "evidence_url": entry["url"] or repo_url,
                    "github_stars": stars,
                    "github_forks": data.get("forks_count", 0),
                    "arxiv_title": entry["title"],
                    "arxiv_published": entry["published"],
                }
            )
            if len(candidates) >= max_results:
                break
        return candidates
    finally:
        if owns_client:
            await client.aclose()
