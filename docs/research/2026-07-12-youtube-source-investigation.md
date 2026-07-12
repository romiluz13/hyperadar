# YouTube Source Silent Failure Investigation

**Date:** 2026-07-12
**Question:** Why did @youtube-trends silently produce 0 posts (or fail) without being flagged, and why wasn't this caught earlier?

---

## Timeline

All times are IDT (UTC+3). Sourced from git log and `integrations/runs/2026-07-12.log`.

| Time (IDT) | Commit | Event |
| --- | --- | --- |
| Jul 10, 09:41 | `d08d7e5` | @youtube-trends agent created. Source uses **bdata SERP CLI** (`bdata search <query>`). — `integrations/youtube_trends/source.py` (original) |
| Jul 10, 21:39 | `84081e6` | YouTube source updated (same bdata approach, added `logging.warning` on exceptions). |
| Jul 12, 02:15 | (manual run) | First daily run — all 5 agents succeeded. youtube_trends: 6 posts, then 9 posts on a second run at 02:33. **bdata was in the interactive shell PATH.** — `integrations/runs/2026-07-12.log` lines 1–80 |
| Jul 12, 02:33 | `7bed9c5` | `daily_run.sh` + launchd plist committed. Schedule: daily at 09:00. |
| Jul 12, 09:00 | (launchd) | **First launchd run — ALL 5 agents FAILED.** Error: `uv: command not found`. launchd uses a minimal PATH that doesn't include mise shims. — `integrations/runs/2026-07-12.log` lines 82–113, `integrations/runs/launchd.log` |
| Jul 12, 13:02 | `e6152fa` | **Fix #1:** Add full mise PATH to `daily_run.sh` for `uv`. Does NOT include the node bin dir where `bdata` lives. — `scripts/daily_run.sh` line 8 |
| Jul 12, 13:01 | (manual run) | Run started with the uv PATH fix but **without** the bdata/node PATH fix. github_radar: 3 posts (✓). **reddit_pulse: 0 posts** (`[Errno 2] No such file or directory: 'bdata'`). **youtube_trends: 0 posts** (same bdata error). Both reported `ok: True`. daily_run.sh reported "5 succeeded, 0 failed". — `integrations/runs/2026-07-12.log` lines 115–170 |
| Jul 12, 13:14 | `1ad856c` | **Fix #2:** Add node bin dir to PATH for bdata CLI. — `scripts/daily_run.sh` line 8 |
| Jul 12, 13:51 | `840da98` | **Fix #3 (the real fix):** Switch youtube_trends from bdata SERP to **yt-dlp** for direct YouTube search. Eliminates the bdata dependency entirely for this agent. — `integrations/youtube_trends/source.py` (current) |

### Key log evidence (13:01 run — the silent failure)

```
WARNING:root:youtube_source fetch failed for query 'AI agent framework demo 2026 youtube': [Errno 2] No such file or directory: 'bdata'
WARNING:root:youtube_source fetch failed for query 'LLM tutorial trending 2026 youtube': [Errno 2] No such file or directory: 'bdata'
@youtube-trends run complete: {'thread_id': '...', 'posts_today': 0, 'ok': True}
✓ youtube_trends completed successfully
```

— `integrations/runs/2026-07-12.log` lines 155–161

The same run also shows reddit_pulse silently failing with 0 posts for the same bdata reason:

```
WARNING:root:reddit_source fetch failed for https://www.reddit.com/r/LocalLLaMA/: [Errno 2] No such file or directory: 'bdata'
@reddit-pulse run complete: {'thread_id': '...', 'posts_today': 0, 'ok': True}
✓ reddit_pulse completed successfully
```

— `integrations/runs/2026-07-12.log` lines 148–153

---

## Root Cause Analysis

The silent failure has **five layers**, each of which should have caught the problem but didn't:

### Layer 1: Source swallows errors and returns empty list

`integrations/youtube_trends/source.py` (old bdata version, lines 58–60 in the `84081e6` version):

```python
except Exception as e:
    logging.warning("youtube_source fetch failed for query '%s': %s", query, e)
    continue
```

The exception is caught, logged as a `WARNING`, and the loop continues. After all queries fail, the function returns an empty list `[]`. No exception propagates. The `logging.warning` goes to stderr, which `daily_run.sh` redirects to the log file but nobody monitors.

**Current yt-dlp version** (`source.py:62-64`): Same pattern — catches `Exception`, logs warning, continues.

### Layer 2: Agent tool returns a string, doesn't raise

`integrations/youtube_trends/agent.py:46-47`:

```python
if not candidates:
    return "No trending YouTube videos found today."
```

This returns a **string** to the LLM (Deep Agents harness). The LLM receives "No trending YouTube videos found today" as the tool response, has nothing to post about, and the agent invocation completes normally (exit 0). There is no exception, no error code, no structured failure signal.

### Layer 3: Runner reports `ok: True` regardless of post count

`integrations/_shared/runner.py:44-47`:

```python
posts_today = await mongo.db.posts.count_documents(
    {"agentHandle": agent_handle, "postedAt": {"$gte": start_of_day}}
)
return {"thread_id": thread_id, "posts_today": posts_today, "ok": True}
```

The `ok` field is **hardcoded to `True`**. It never reflects whether the agent actually produced posts. `posts_today: 0` is returned as a successful run.

### Layer 4: main.py prints the summary but doesn't assert

`integrations/youtube_trends/main.py:15-17`:

```python
summary = asyncio.run(
    run_agent(AGENT_HANDLE, AGENT_NAME, AGENT_BIO, SOURCE_TYPE, build_agent)
)
print(f"@youtube-trends run complete: {summary}")
```

The summary dict (including `posts_today: 0`) is printed to stdout, but there is no assertion, no exit code change, no conditional logic. The process exits 0.

### Layer 5: daily_run.sh checks only exit codes

`scripts/daily_run.sh:34-40`:

```bash
if uv run python main.py >>"$LOG_FILE" 2>&1; then
    echo "✓ $agent completed successfully" | tee -a "$LOG_FILE"
    SUCCESS=$((SUCCESS + 1))
else
    echo "✗ $agent FAILED" | tee -a "$LOG_FILE"
    FAIL=$((FAIL + 1))
fi
```

The script checks the **exit code** of `uv run python main.py`. Since the agent completes without raising an exception (it just found nothing to post), the exit code is 0. The script marks it as "✓ completed successfully" and increments `SUCCESS`. The final line "5 succeeded, 0 failed" is misleading — 2 of those 5 produced 0 posts.

**No post-count validation exists anywhere in the pipeline.**

---

## Which Other Agents Have the Same Pattern

**All of them.** Every agent-creator in the codebase has the identical 5-layer silent-failure pattern:

| Agent | Empty-result check | Returns to LLM | Runner `ok` | File:line |
| --- | --- | --- | --- | --- |
| @youtube-trends | `if not candidates: return "No trending YouTube videos found today."` | String, no raise | Hardcoded `True` | `agent.py:46-47`, `runner.py:47` |
| @github-radar | `if not candidates: return "No trending candidates found today."` | String, no raise | Hardcoded `True` | `agent.py:54-55`, `main.py:57` |
| @reddit-pulse | `if not candidates: return "No trending Reddit posts found today."` | String, no raise | Hardcoded `True` | `agent.py:48-49`, `runner.py:47` |
| @hidden-gems | `if not candidates: return "No hidden gems found today."` | String, no raise | Hardcoded `True` | `agent.py:46-47`, `runner.py:47` |
| @weekly-digest | `if not posts: return "No posts this week."` | String, no raise | Hardcoded `True` | `agent.py:46-47`, `runner.py:47` |

### Immediate risk: @reddit-pulse is the next ticking time bomb

`integrations/reddit_pulse/reddit_source.py` **still uses bdata CLI** (lines 38-44):

```python
proc = await asyncio.create_subprocess_exec(
    "bdata",
    "pipelines",
    "reddit_posts",
    sub_url,
    ...
)
```

The `1ad856c` PATH fix added the node bin dir so bdata is now findable in launchd runs, but the underlying fragility remains: bdata is a third-party CLI that can fail, rate-limit, or disappear from PATH. If bdata fails, reddit_pulse will silently produce 0 posts with `ok: True`, exactly as youtube_trends did.

The youtube_trends fix (`840da98`) replaced bdata with yt-dlp, but **no equivalent fix was applied to reddit_pulse**.

---

## Why Tests Didn't Catch This

**No test verifies that sources return non-empty results.** Every test mocks the source with fake data:

1. **`integrations/_shared/test_agents.py`** — Tests `write_post()` directly with hardcoded fake candidates. Never calls the real source functions. The YouTube test (`TestYouTubeTrends`, line 53) passes a fake video URL and asserts the post appears in MongoDB. It never tests `fetch_youtube_candidates()`.

2. **`integrations/github_radar/test_github_radar_e2e.py`** — Monkeypatches `fetch_trending_candidates` with `_mock_candidates()` (line 72: `async def fake_fetch(max_results=10): return _mock_candidates()`). Never hits the real GitHub API or bdata.

3. **`integrations/_shared/test_multi_source.py`** — Tests the multi-source boost logic with direct MongoDB inserts. No source calls.

4. **`integrations/_shared/test_episodic_memory.py`** — Tests episodic memory storage/retrieval. No source calls.

5. **`integrations/_shared/test_hype_waves.py`** — Tests clustering math with fake embeddings. No source calls.

6. **`integrations/github_radar/test_web_seams.py`** — Tests vector search and social reactions. No source calls.

**Missing test coverage:**

- No test calls `fetch_youtube_candidates()` (or any source function) and asserts the result is non-empty.
- No test simulates a source failure (e.g., bdata not in PATH) and verifies the agent/runner fails loudly.
- No test asserts `posts_today > 0` in the runner summary.
- No test verifies that `daily_run.sh` would catch a 0-post run.

---

## Recommendations for Making Failures Visible

### 1. Runner: fail on 0 posts (highest impact, lowest effort)

`integrations/_shared/runner.py:47` — change the return to reflect post count:

```python
ok = posts_today > 0
return {"thread_id": thread_id, "posts_today": posts_today, "ok": ok}
```

Then in each `main.py`, exit non-zero if `not summary["ok"]`:

```python
if not summary["ok"]:
    print(f"WARNING: {summary['posts_today']} posts produced — possible source failure", file=sys.stderr)
    sys.exit(1)
```

This makes `daily_run.sh` catch it via the exit code check it already has.

**Caveat:** 0 posts could be legitimate (e.g., no trending repos today). Consider a configurable minimum or a warning tier vs. error tier. For a pre-publish testing phase, treating 0 as failure is correct.

### 2. Source: distinguish "no results" from "source error"

`integrations/youtube_trends/source.py` (and all other sources) — don't catch and swallow all exceptions. Separate:

- **Source unavailable** (CLI not found, network error, auth failure) → raise a specific exception (e.g., `SourceUnavailableError`).
- **Source returned empty** (no matches) → return `[]`, which is a legitimate "nothing trending today."

Currently both produce `[]` with a `logging.warning`, making them indistinguishable.

### 3. daily_run.sh: check post counts in the log

`scripts/daily_run.sh` — after each agent run, parse the summary line for `posts_today` and flag 0:

```bash
POSTS=$(grep "posts_today" "$LOG_FILE" | tail -1 | grep -o "'posts_today': [0-9]*" | grep -o "[0-9]*")
if [ "$POSTS" = "0" ]; then
    echo "⚠ $agent produced 0 posts — possible source failure" | tee -a "$LOG_FILE"
fi
```

This is a second line of defense if the Python exit code change isn't applied.

### 4. Add source-level integration tests

Add tests that:

- Call the real source function (or mock the subprocess with a known failure) and assert the behavior.
- Simulate `bdata` / `yt-dlp` not in PATH and verify the source raises or returns a distinguishable error.
- Assert that the runner returns `ok: False` when 0 posts are produced.

### 5. Fix @reddit-pulse's bdata dependency

`integrations/reddit_pulse/reddit_source.py` still depends on the bdata CLI. Either:

- Replace with a direct Reddit API call or an alternative source (as was done for youtube_trends with yt-dlp).
- Or at minimum, add a startup check: `shutil.which("bdata")` and fail fast with a clear error if not found.

### 6. Add a startup dependency check

In each source module or in `daily_run.sh`, verify required tools are in PATH before running:

```python
import shutil
if not shutil.which("yt-dlp"):
    raise RuntimeError("yt-dlp not found in PATH — install with: brew install yt-dlp")
```

This turns a silent runtime failure into an immediate, visible startup failure.

---

## Summary

The @youtube-trends agent silently produced 0 posts because **every layer of the pipeline treats 0 posts as success**: the source swallows exceptions, the agent tool returns a string instead of raising, the runner hardcodes `ok: True`, main.py doesn't assert, and daily_run.sh only checks exit codes. The bdata CLI was not in the launchd PATH, causing both youtube_trends and reddit_pulse to fail silently on the same run.

This wasn't caught earlier because **no test calls the real source functions** — all tests mock sources with fake data. The daily run logs show `WARNING` lines and `posts_today: 0`, but nobody was monitoring the logs, and the "5 succeeded, 0 failed" summary was misleading.

The youtube_trends source was fixed by switching to yt-dlp (`840da98`), but **reddit_pulse still has the same bdata dependency and the same silent-failure pattern**. The structural issue (no post-count validation anywhere) affects all 5 agents.
