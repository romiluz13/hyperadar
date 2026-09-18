"""Tests for arXiv discovery in hidden_gems/source.py.

Pure parser tests + an end-to-end fetch_arxiv_candidates run against a
routing fake client — no network.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from hidden_gems import source
from hidden_gems.source import (
    _extract_repo_url,
    _published_within_days,
    parse_arxiv_entries,
)

_ATOM = "http://www.w3.org/2005/Atom"


def _entry(title, summary, published, url="https://arxiv.org/abs/2401.00001"):
    return (
        f"<entry><title>{title}</title><summary>{summary}</summary>"
        f"<published>{published}</published>"
        f'<link href="{url}" /></entry>'
    )


def _feed(*entries):
    return (
        '<?xml version="1.0"?>'
        f'<feed xmlns="{_ATOM}">' + "".join(entries) + "</feed>"
    )


def _days_ago(days):
    return (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── parse_arxiv_entries ───


def test_parse_arxiv_entries_parses_fields():
    xml = _feed(
        _entry("Cool Paper", "  We propose   something new. Code available.", _days_ago(1)),
        _entry("Second Paper", "Another abstract.", _days_ago(2)),
    )
    entries = parse_arxiv_entries(xml)
    assert len(entries) == 2
    assert entries[0]["title"] == "Cool Paper"
    # Whitespace is collapsed by the cleaner.
    assert entries[0]["summary"] == "We propose something new. Code available."
    assert entries[0]["url"] == "https://arxiv.org/abs/2401.00001"


def test_parse_arxiv_entries_invalid_xml_returns_empty():
    assert parse_arxiv_entries("<not-xml") == []
    assert parse_arxiv_entries("") == []


def test_parse_arxiv_entries_drops_incomplete_entries():
    xml = _feed(
        _entry("No summary", "", _days_ago(1)),
        _entry("Real one", "Has summary.", _days_ago(1)),
    )
    entries = parse_arxiv_entries(xml)
    assert [e["title"] for e in entries] == ["Real one"]


# ─── _extract_repo_url ───


def test_extract_repo_url_from_summary():
    text = "Code is available at https://github.com/owner/repo."
    assert _extract_repo_url(text) == "https://github.com/owner/repo"


def test_extract_repo_url_no_link_returns_none():
    assert _extract_repo_url("no code here") is None
    assert _extract_repo_url("") is None


def test_extract_repo_url_strips_git_suffix():
    """Clone URLs name the repo page, not a .git path."""
    assert (
        _extract_repo_url("git clone https://github.com/owner/repo.git")
        == "https://github.com/owner/repo"
    )


def test_extract_repo_url_is_case_insensitive():
    """Paper abstracts write GitHub.com in any case."""
    assert (
        _extract_repo_url("Code at https://GitHub.com/Owner/Repo")
        == "https://github.com/Owner/Repo"
    )


def test_extract_repo_url_ignores_deep_paths():
    # Only owner/repo counts — not a file tree path.
    text = "see github.com/owner/repo/tree/main/src"
    assert _extract_repo_url(text) == "https://github.com/owner/repo"


# ─── _published_within_days ───


def test_published_within_days_fresh():
    now = datetime.now(timezone.utc)
    assert _published_within_days(_days_ago(2), now, 14) is True


def test_published_within_days_stale():
    now = datetime.now(timezone.utc)
    assert _published_within_days(_days_ago(30), now, 14) is False


def test_published_within_days_garbage_date():
    now = datetime.now(timezone.utc)
    assert _published_within_days("not-a-date", now, 14) is False


# ─── fetch_arxiv_candidates (routed fake client) ───


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {self.status_code}")


class _RouteClient:
    """Serves canned responses keyed by the request URL's path."""

    def __init__(self, routes):
        self._routes = routes  # {(url path prefix): response}
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        for key, response in self._routes.items():
            if key in url:
                return response
        return _Response(status_code=404)


def _repo_payload(full_name, stars, forks=5, description="Research code."):
    return {
        "full_name": full_name,
        "stargazers_count": stars,
        "forks_count": forks,
        "description": description,
    }


@pytest.mark.asyncio
async def test_fetch_arxiv_candidates_end_to_end():
    xml = _feed(
        # Fresh paper, linked repo clears the star floor → candidate.
        _entry(
            "Hot Paper",
            "Great results. Code at https://github.com/owner/hot-repo.",
            _days_ago(2),
            "https://arxiv.org/abs/2401.00001",
        ),
        # Duplicate link — same repo, second paper → deduped.
        _entry(
            "Hot Paper v2",
            "Also uses https://github.com/owner/hot-repo.",
            _days_ago(1),
            "https://arxiv.org/abs/2401.00002",
        ),
        # Fresh paper, repo below the 10-star traction floor → dropped.
        _entry(
            "Quiet Paper",
            "Code at https://github.com/owner/quiet-repo.",
            _days_ago(3),
            "https://arxiv.org/abs/2401.00003",
        ),
        # Stale paper (older than 14 days) → dropped before enrichment.
        _entry(
            "Old Paper",
            "Code at https://github.com/owner/old-repo.",
            _days_ago(30),
            "https://arxiv.org/abs/2401.00004",
        ),
        # Fresh paper, no GitHub link → dropped.
        _entry(
            "Theory Paper",
            "Proofs only, no code.",
            _days_ago(1),
            "https://arxiv.org/abs/2401.00005",
        ),
        # Fresh paper, GitHub enrich 404s → dropped.
        _entry(
            "Ghost Paper",
            "Code at https://github.com/owner/gone-repo.",
            _days_ago(1),
            "https://arxiv.org/abs/2401.00006",
        ),
    )

    client = _RouteClient(
        {
            "export.arxiv.org": _Response(text=xml),
            "repos/owner/hot-repo": _Response(
                payload=_repo_payload("owner/hot-repo", 42)
            ),
            "repos/owner/quiet-repo": _Response(
                payload=_repo_payload("owner/quiet-repo", 3)
            ),
            "repos/owner/old-repo": _Response(
                payload=_repo_payload("owner/old-repo", 500)
            ),
            "repos/owner/gone-repo": _Response(status_code=404),
        }
    )

    candidates = await source.fetch_arxiv_candidates(max_results=8, client=client)

    assert [c["url"] for c in candidates] == ["https://github.com/owner/hot-repo"]
    hot = candidates[0]
    assert hot["discovery_source"] == "arxiv"
    assert hot["github_stars"] == 42
    assert hot["arxiv_title"] == "Hot Paper"
    assert hot["evidence_url"] == "https://arxiv.org/abs/2401.00001"

    # The stale repo is dropped BEFORE enrichment — no GitHub call for it.
    called_urls = [u for u, _ in client.calls]
    assert not any("old-repo" in u for u in called_urls)
    # The duplicate paper's repo is deduped — only one enrich call for hot-repo.
    assert sum("hot-repo" in u for u in called_urls) == 1


@pytest.mark.asyncio
async def test_fetch_arxiv_candidates_respects_max_results():
    entries = [
        _entry(
            f"Paper {i}",
            f"Code at https://github.com/owner/repo-{i}.",
            _days_ago(1),
            f"https://arxiv.org/abs/2401.0000{i}",
        )
        for i in range(5)
    ]
    routes = {"export.arxiv.org": _Response(text=_feed(*entries))}
    for i in range(5):
        routes[f"repos/owner/repo-{i}"] = _Response(
            payload=_repo_payload(f"owner/repo-{i}", 50)
        )
    client = _RouteClient(routes)

    candidates = await source.fetch_arxiv_candidates(max_results=2, client=client)
    assert len(candidates) == 2
    assert candidates[0]["url"] == "https://github.com/owner/repo-0"
    assert candidates[1]["url"] == "https://github.com/owner/repo-1"
