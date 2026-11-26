"""Tests for the trending source, run on recorded github.com/trending pages.

The fixtures in testdata/trending were captured 2026-11-26; the expected
rows below are what a person reads on those pages, not values
recomputed by the parser.
"""

from pathlib import Path

import httpx
import pytest

_FIXTURES = Path(__file__).parent / "testdata" / "trending"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text()


class _FakeResponse:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code != 200:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None
            )


def _serve(monkeypatch, pages: dict):
    """Patch httpx.AsyncClient to answer each URL from ``pages``.

    A value is the HTML to serve, or a list of HTML strings / exceptions /
    status codes served on successive requests. Every request is recorded.
    """
    import trending_source

    calls: list[tuple[str, dict]] = []

    class FakeClient:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url, **kw):
            calls.append((url, kw))
            answer = pages[url]
            if isinstance(answer, list):
                answer = answer.pop(0)
            if isinstance(answer, Exception):
                raise answer
            if isinstance(answer, int):
                return _FakeResponse(status=answer)
            return _FakeResponse(answer)

    monkeypatch.setattr(trending_source.httpx, "AsyncClient", FakeClient)
    return calls


@pytest.fixture
def sleeps(monkeypatch):
    import trending_source

    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(trending_source.asyncio, "sleep", fake_sleep)
    return slept


_ALL_DAILY = "https://github.com/trending?since=daily"
_ALL_WEEKLY = "https://github.com/trending?since=weekly"
_PYTHON_DAILY = "https://github.com/trending/python?since=daily"
_TYPESCRIPT_DAILY = "https://github.com/trending/typescript?since=daily"
_RUST_DAILY = "https://github.com/trending/rust?since=daily"


@pytest.mark.parametrize(
    "language, since, url, fixture, count, first_row",
    [
        (
            None,
            "daily",
            _ALL_DAILY,
            "trending-daily.html",
            16,
            ("elementalsouls/Claude-BugHunter", "Python", 24_333, 2_476, 1_600),
        ),
        (
            None,
            "weekly",
            _ALL_WEEKLY,
            "trending-weekly.html",
            15,
            ("backnotprop/plannotator", "TypeScript", 15_232, 1_457, 1_764),
        ),
        (
            "python",
            "daily",
            _PYTHON_DAILY,
            "trending-python-daily.html",
            24,
            ("elementalsouls/Claude-BugHunter", "Python", 24_333, 2_476, 1_600),
        ),
        (
            "typescript",
            "daily",
            _TYPESCRIPT_DAILY,
            "trending-typescript-daily.html",
            17,
            ("cursor/plugins", "TypeScript", 19_755, 2_136, 268),
        ),
        (
            "rust",
            "daily",
            _RUST_DAILY,
            "trending-rust-daily.html",
            14,
            ("janhq/jan", "Rust", 46_206, 3_274, 71),
        ),
    ],
)
async def test_each_fixture_parses_to_its_observed_rows(
    monkeypatch, language, since, url, fixture, count, first_row
):
    import trending_source

    _serve(monkeypatch, {url: _fixture(fixture)})
    page = trending_source.TrendingPage(language=language, since=since)

    rows = await trending_source.fetch_trending(pages=(page,))

    assert len(rows) == count
    assert [r.rank for r in rows] == list(range(1, count + 1))
    assert {r.page for r in rows} == {page}
    first = rows[0]
    assert (
        first.slug,
        first.language,
        first.stars,
        first.forks,
        first.window_stars,
    ) == first_row
    assert first.window == since


async def test_first_rows_carry_the_numbers_printed_on_the_page(monkeypatch):
    import trending_source

    _serve(
        monkeypatch,
        {
            _ALL_DAILY: _fixture("trending-daily.html"),
            _ALL_WEEKLY: _fixture("trending-weekly.html"),
        },
    )
    daily = trending_source.TrendingPage(language=None, since="daily")
    weekly = trending_source.TrendingPage(language=None, since="weekly")

    rows = await trending_source.fetch_trending(pages=(daily, weekly))

    first_daily = rows[0]
    assert first_daily == trending_source.TrendingRow(
        page=daily,
        rank=1,
        slug="elementalsouls/Claude-BugHunter",
        description=first_daily.description,
        language="Python",
        stars=24_333,
        forks=2_476,
        window_stars=1_600,
        window="daily",
    )
    assert first_daily.description.startswith(
        "A Claude Code skill bundle for bug hunting and external red-team work"
    )
    first_weekly = next(r for r in rows if r.page == weekly)
    assert (
        first_weekly.slug,
        first_weekly.language,
        first_weekly.stars,
        first_weekly.forks,
        first_weekly.window_stars,
        first_weekly.window,
    ) == ("backnotprop/plannotator", "TypeScript", 15_232, 1_457, 1_764, "weekly")


async def test_descriptions_are_plain_text_with_entities_unescaped(monkeypatch):
    import trending_source

    _serve(monkeypatch, {_ALL_DAILY: _fixture("trending-daily.html")})
    daily = trending_source.TrendingPage(language=None, since="daily")

    rows = await trending_source.fetch_trending(pages=(daily,))

    by_slug = {r.slug: r.description for r in rows}
    assert by_slug["obra/superpowers"] == (
        "An agentic skills framework & software development methodology that works."
    )
    assert by_slug["HKUDS/CLI-Anything"].startswith(
        '"CLI-Anything: Making ALL Software Agent-Native" -- CLI-Hub:'
    )


def _first_articles(html: str, keep: int) -> str:
    opener = '<article class="Box-row">'
    return opener.join(html.split(opener)[: keep + 1])


@pytest.mark.parametrize(
    "mutate, cause",
    [
        (lambda h: h.replace('class="Box-row"', 'class="Box-row-v2"'), "Box-row"),
        (lambda h: h.replace('/stargazers"', '/stars"'), "total stars"),
        (lambda h: h.replace('/forks"', '/network"'), "forks"),
        (lambda h: h.replace(" stars today", " starred today"), "stars today"),
        (lambda h: h.replace('class="h3 lh-condensed"', 'class="h3"'), "slug"),
        (lambda h: _first_articles(h, 4), "4 rows"),
    ],
    ids=["no-box-row", "no-stargazers", "no-forks", "no-window", "no-slug", "4-rows"],
)
async def test_changed_markup_raises_naming_the_cause(monkeypatch, mutate, cause):
    import trending_source

    _serve(monkeypatch, {_ALL_DAILY: mutate(_fixture("trending-daily.html"))})
    daily = trending_source.TrendingPage(language=None, since="daily")

    with pytest.raises(trending_source.TrendingMarkupChanged, match=cause):
        await trending_source.fetch_trending(pages=(daily,))


def _default_site(**overrides):
    site = {
        _ALL_DAILY: _fixture("trending-daily.html"),
        _ALL_WEEKLY: _fixture("trending-weekly.html"),
        _PYTHON_DAILY: _fixture("trending-python-daily.html"),
        _TYPESCRIPT_DAILY: _fixture("trending-typescript-daily.html"),
    }
    site.update(overrides)
    return site


async def test_default_run_reads_the_four_configured_pages(monkeypatch):
    import trending_source

    calls = _serve(monkeypatch, _default_site())

    rows = await trending_source.fetch_trending()

    assert [url for url, _ in calls] == [
        _ALL_DAILY,
        _ALL_WEEKLY,
        _PYTHON_DAILY,
        _TYPESCRIPT_DAILY,
    ]
    assert len(rows) == 16 + 15 + 24 + 17


async def test_a_failed_all_languages_page_raises(monkeypatch, sleeps):
    import trending_source

    _serve(monkeypatch, _default_site(**{_ALL_WEEKLY: [503, 503]}))

    with pytest.raises(trending_source.TrendingUnavailable, match="since=weekly"):
        await trending_source.fetch_trending()


@pytest.mark.parametrize(
    "failure",
    [[503, 503], "<html><body>no rows</body></html>"],
    ids=["http-error", "markup-changed"],
)
async def test_a_failed_language_page_only_warns(monkeypatch, sleeps, caplog, failure):
    import trending_source

    _serve(monkeypatch, _default_site(**{_PYTHON_DAILY: failure}))

    with caplog.at_level("WARNING"):
        rows = await trending_source.fetch_trending()

    assert len(rows) == 16 + 15 + 17
    assert all(r.page.language != "python" for r in rows)
    assert any(
        "trending/python" in rec.getMessage() and rec.levelname == "WARNING"
        for rec in caplog.records
    )


async def test_requests_send_a_generic_user_agent_and_no_token(monkeypatch):
    import trending_source

    calls = _serve(monkeypatch, _default_site())

    await trending_source.fetch_trending()

    for _url, kw in calls:
        headers = {k.lower(): v for k, v in kw.get("headers", {}).items()}
        agent = headers.get("user-agent", "")
        assert agent and not agent.startswith("python-httpx"), agent
        for identifier in ("hyperadar", "iluz", "rombot", "rom-", "github.com/"):
            assert identifier not in agent.lower(), agent
        assert "authorization" not in headers


async def test_a_failed_request_is_retried_once_after_30_seconds(monkeypatch, sleeps):
    import trending_source

    calls = _serve(
        monkeypatch,
        _default_site(
            **{
                _ALL_DAILY: [503, _fixture("trending-daily.html")],
                _PYTHON_DAILY: [
                    httpx.ConnectError("reset"),
                    _fixture("trending-python-daily.html"),
                ],
            }
        ),
    )

    rows = await trending_source.fetch_trending()

    assert len(rows) == 16 + 15 + 24 + 17
    assert sleeps == [30, 30]
    assert [url for url, _ in calls].count(_ALL_DAILY) == 2


async def test_only_one_retry_is_made(monkeypatch, sleeps):
    import trending_source

    calls = _serve(
        monkeypatch,
        _default_site(**{_ALL_DAILY: [503, 503, _fixture("trending-daily.html")]}),
    )

    with pytest.raises(trending_source.TrendingUnavailable):
        await trending_source.fetch_trending()

    assert [url for url, _ in calls].count(_ALL_DAILY) == 2
    assert sleeps == [30]


async def test_more_than_six_pages_is_refused_before_any_fetch(monkeypatch):
    import trending_source

    calls = _serve(monkeypatch, {})
    pages = tuple(
        trending_source.TrendingPage(language=lang, since="daily")
        for lang in ("python", "typescript", "rust", "go", "java", "c", "zig")
    )

    with pytest.raises(ValueError, match="6"):
        await trending_source.fetch_trending(pages=pages)

    assert calls == []


def _spoof_window_in_description(html: str) -> str:
    html = html.replace("        1,600 stars today\n</span>", "</span>", 1)
    return html.replace(
        "A Claude Code skill bundle",
        "Hit 9,999 stars today. A Claude Code skill bundle",
        1,
    )


@pytest.mark.parametrize(
    "mutate, cause",
    [
        (_spoof_window_in_description, "stars today"),
        (lambda h: h.replace("\n        24,333</a>", "</a>", 1), "total stars"),
    ],
    ids=["window-only-in-description", "stars-number-missing"],
)
async def test_a_missing_number_is_not_borrowed_from_elsewhere_in_the_row(
    monkeypatch, mutate, cause
):
    import trending_source

    _serve(monkeypatch, {_ALL_DAILY: mutate(_fixture("trending-daily.html"))})
    daily = trending_source.TrendingPage(language=None, since="daily")

    with pytest.raises(trending_source.TrendingMarkupChanged, match=cause):
        await trending_source.fetch_trending(pages=(daily,))


async def test_retries_count_toward_the_six_page_fetch_ceiling(
    monkeypatch, sleeps, caplog
):
    import trending_source

    languages = ("python", "typescript", "rust", "go")
    pages = tuple(
        trending_source.TrendingPage(language=lang, since="daily") for lang in languages
    )
    calls = _serve(
        monkeypatch,
        {page.url: [503, 503] for page in pages},
    )

    with caplog.at_level("WARNING"):
        rows = await trending_source.fetch_trending(pages=pages)

    assert rows == []
    assert len(calls) == 6
    assert sleeps == [30, 30]
