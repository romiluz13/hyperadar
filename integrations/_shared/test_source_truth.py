import importlib.util
from pathlib import Path

import pytest


class FakeProcess:
    def __init__(self, output: str):
        self.output = output

    async def communicate(self):
        return self.output.encode(), b""


def load_source(relative_path: str, module_name: str):
    path = Path(__file__).parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_youtube_candidate_preserves_real_views_without_a_star_proxy(monkeypatch):
    source = load_source("youtube_trends/source.py", "youtube_truth_source")
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/yt-dlp")

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess("abc123|A complete title|Signal Channel|285000|720\n")

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    candidates = await source.fetch_youtube_candidates(max_results=1)

    assert candidates[0]["viewCount"] == 285000
    assert "stars" not in candidates[0]


@pytest.mark.asyncio
async def test_reddit_candidate_labels_search_visibility_as_a_proxy(monkeypatch):
    source = load_source("reddit_pulse/reddit_source.py", "reddit_truth_source")
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/bdata")

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess(
            "1 | A complete Reddit title | "
            "https://www.reddit.com/r/LocalLLaMA/comments/abc/a_complete_title/ | "
            "Search result description\n"
        )

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    candidates = await source.fetch_reddit_candidates(max_results=1)

    assert candidates[0]["serp_rank"] == 1
    assert candidates[0]["visibility_score"] == 90
    assert "upvotes" not in candidates[0]
    assert "num_comments" not in candidates[0]


def test_port_catalog_errors_cannot_be_silenced():
    from _shared.port_client import require_success

    success = {"ok": True, "entity": {"identifier": "project"}}
    assert require_success(success, "sync project") is success

    with pytest.raises(RuntimeError, match="sync project.*network unavailable"):
        require_success(
            {"ok": False, "error": "network_error", "message": "network unavailable"},
            "sync project",
        )
