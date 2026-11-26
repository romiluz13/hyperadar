"""The trending source: github.com/trending pages as parsed rows (ADR 0007).

The trending HTML is unofficial, so the parser anchors only on the markup
observed stable across daily, weekly and language pages and raises when it
no longer finds it, instead of returning a quiet day.
"""

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

MAX_PAGES = 6
_MIN_ROWS = 5
_RETRY_DELAY_SECONDS = 30
# Generic on purpose: no user, project or token identifies the reader. English
# is pinned because the parser reads the "N stars today|this week" text.
_HEADERS = {
    "User-Agent": "trending-digest-reader/1.0 (one daily read of the public trending pages)",
    "Accept-Language": "en",
}

_ARTICLE_RE = re.compile(r'<article class="Box-row">(.*?)</article>', re.DOTALL)
_SLUG_RE = re.compile(
    r'<h2 class="h3 lh-condensed">\s*<a\b[^>]*?\shref="/([^"/]+/[^"/]+)"'
)
_DESCRIPTION_RE = re.compile(
    r'<p class="col-9 color-fg-muted my-1[^"]*">(.*?)</p>', re.DOTALL
)
_LANGUAGE_RE = re.compile(r'itemprop="programmingLanguage">([^<]+)<')
# The number must sit between the icon's </svg> and the closing tag, so a
# description that mentions "N stars today" cannot stand in for a missing one.
_WINDOW_RE = re.compile(r"</svg>\s*([\d,]+) stars? (today|this week)\s*</span>")
_TAG_RE = re.compile(r"<[^>]+>")
_WINDOWS = {"today": "daily", "this week": "weekly"}


class TrendingUnavailable(Exception):
    """No trending rows this run; discovery falls back to REST search."""


class TrendingMarkupChanged(TrendingUnavailable):
    """The page no longer has the markup the parser anchors on."""


@dataclass(frozen=True)
class TrendingPage:
    language: str | None
    since: str

    @property
    def url(self) -> str:
        path = f"/{self.language}" if self.language else ""
        return f"https://github.com/trending{path}?since={self.since}"

    @property
    def label(self) -> str:
        return f"{self.language or 'all'}/{self.since}"


@dataclass(frozen=True)
class TrendingRow:
    page: TrendingPage
    rank: int
    slug: str
    description: str
    language: str | None
    stars: int
    forks: int
    window_stars: int
    window: str


DEFAULT_PAGES = (
    TrendingPage(language=None, since="daily"),
    TrendingPage(language=None, since="weekly"),
    TrendingPage(language="python", since="daily"),
    TrendingPage(language="typescript", since="daily"),
)


def _number(text: str) -> int:
    return int(text.replace(",", ""))


def _count_link(block: str, slug: str, suffix: str) -> int | None:
    match = re.search(
        rf'href="/{re.escape(slug)}/{suffix}"[^>]*>(?:(?!</a>).)*</svg>\s*([\d,]+)\s*</a>',
        block,
        re.DOTALL,
    )
    return _number(match.group(1)) if match else None


def _parse_row(page: TrendingPage, rank: int, block: str) -> TrendingRow:
    slug_match = _SLUG_RE.search(block)
    if not slug_match:
        raise TrendingMarkupChanged(
            f"{page.url} row {rank}: no slug in the h3 lh-condensed heading"
        )
    slug = slug_match.group(1)
    where = f"{page.url} row {rank} ({slug})"
    stars = _count_link(block, slug, "stargazers")
    if stars is None:
        raise TrendingMarkupChanged(f"{where}: no total stars (/stargazers link)")
    forks = _count_link(block, slug, "forks")
    if forks is None:
        raise TrendingMarkupChanged(f"{where}: no forks (/forks link)")
    window = _WINDOW_RE.search(block)
    if not window:
        raise TrendingMarkupChanged(f"{where}: no 'N stars today|this week' text")
    description = _DESCRIPTION_RE.search(block)
    language = _LANGUAGE_RE.search(block)
    return TrendingRow(
        page=page,
        rank=rank,
        slug=slug,
        description=html.unescape(_TAG_RE.sub("", description.group(1))).strip()
        if description
        else "",
        language=language.group(1).strip() if language else None,
        stars=stars,
        forks=forks,
        window_stars=_number(window.group(1)),
        window=_WINDOWS[window.group(2)],
    )


def _parse_page(page: TrendingPage, html_text: str) -> list[TrendingRow]:
    blocks = _ARTICLE_RE.findall(html_text)
    if not blocks:
        raise TrendingMarkupChanged(f'{page.url}: no <article class="Box-row"> rows')
    rows = [_parse_row(page, rank, block) for rank, block in enumerate(blocks, 1)]
    if len(rows) < _MIN_ROWS:
        raise TrendingMarkupChanged(
            f"{page.url}: only {len(rows)} rows (fewer than {_MIN_ROWS})"
        )
    return rows


async def _get(client: httpx.AsyncClient, url: str) -> str:
    try:
        response = await client.get(url, headers=_HEADERS)
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise TrendingUnavailable(f"{url}: {e}") from e
    return response.text


async def fetch_trending(
    pages: tuple[TrendingPage, ...] = DEFAULT_PAGES,
) -> list[TrendingRow]:
    """Fetch and parse ``pages`` with at most ``MAX_PAGES`` requests, retries
    included. All-languages pages raise on failure; a failed language page is
    logged and skipped."""
    if len(pages) > MAX_PAGES:
        raise ValueError(f"{len(pages)} trending pages configured; at most {MAX_PAGES}")
    retries_left = MAX_PAGES - len(pages)
    rows: list[TrendingRow] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for page in pages:
            try:
                try:
                    html_text = await _get(client, page.url)
                except TrendingUnavailable:
                    if not retries_left:
                        raise
                    retries_left -= 1
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    html_text = await _get(client, page.url)
                rows += _parse_page(page, html_text)
            except TrendingUnavailable as e:
                if page.language is None:
                    raise
                logging.warning("GitHub trending language page skipped: %s", e)
    return rows


async def write_trending_snapshots(
    db, rows: list[TrendingRow], captured_at: datetime | None = None
) -> int:
    """Store one ``trending_snapshots`` document per row.

    ``scripts/setup_mongodb.py`` owns the 30-day TTL index on ``capturedAt``.

    The printed windowed stars are kept only here, as a cross-check against
    star history (CONTEXT.md, Stars this week).
    """
    if not rows:
        return 0
    captured_at = captured_at or datetime.now(timezone.utc)
    result = await db.trending_snapshots.insert_many(
        [
            {
                "date": captured_at.date().isoformat(),
                "page": row.page.label,
                "rank": row.rank,
                "slug": row.slug,
                "windowStars": row.window_stars,
                "description": row.description,
                "language": row.language,
                "capturedAt": captured_at,
            }
            for row in rows
        ]
    )
    return len(result.inserted_ids)
