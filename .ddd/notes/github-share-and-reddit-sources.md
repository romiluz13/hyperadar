# More GitHub in the feed, more Reddit sources — DDD task note

**Status (2026-11-18):** implemented and verified locally on revision `6b10e45a00f8` (sha256 prefix of `git diff HEAD`); not committed, not deployed; live effect unverified until the next CI refresh
**Owner/route:** Matt, Debug then Build, `Cut: none | "full and must use ddd to make sure we do perfect clean work" | user`
**Validation:** DDD. **Task note path:** `.ddd/notes/github-share-and-reddit-sources.md`
**Inputs:** user: "in the last 10 runs or so i get mostly reddit and too less github -- why? anyway fix according to all your findings" and "use /last30days to think if we should pull from more subreddits". Earlier standing constraints (`.ddd/notes/perfect-daily-digest.md`): $0 per run, "improve, not break".

## Requested outcome (Done means)

| # | Done means |
|---|---|
| 1 | The cause of "mostly Reddit, too little GitHub" is measured, not guessed. |
| 2 | A repo that HN discussed through a sub-path link (releases, tree, blob) corroborates `@github-radar`. |
| 3 | `@github-radar` logs how many repos survive each stage, so an empty run explains itself. |
| 4 | `@reddit-pulse` reads r/OpenSourceAI, r/mcp and r/ollama, and keeps their low-upvote launch posts as corroboration snapshots without publishing them as posts. |
| 5 | A subreddit that 403s is retried within the same run, still at $0. |
| 6 | The daily digest reserves up to 2 slots for repo items that clear the existing 50-point floor. |
| 7 | Everything stays $0 per run; Mongo gets no new writes beyond the extra snapshot rows. |

## Measured findings (read-only, 2026-11-18; scripts only in the session scratchpad, no writes)

| Finding | Evidence |
|---|---|
| F0 Share | CI runs 2026-11-10..18 (9 scheduled runs + 1 dispatch): `@github-radar` posts_written 0 in all; `@reddit-pulse` 1-20; `@hidden-gems` 5; `@youtube-trends` 0-2. Prod `posts` by day: github-radar 7 on 11-09, then 0. |
| F1 Momentum is not the blocker | Replay of `fetch_trending_candidates_with_momentum` without writes: pool 748 repos (>=7 snapshots, fresh) → 413 after fake-star filter → threshold 95 (pool-scaled) → 45 pass every behavioural gate; 34 more only blocked by the 14-day cooldown. |
| F2 Corroboration empties the 45 | 0 of 45 pass. Algolia returns any HN story for 3 of 45; current matcher accepts 0; Reddit corpus (958 snapshots, 14 days) names 0 of the top 40 repos. |
| F3 Matcher bug | `ggml-org/ggml`: HN story "Ggml v1.0.0", 420 points, URL `https://github.com/ggml-org/ggml/releases/tag/v1.0.0`. `_story_matches` (`_shared/engagement.py`) requires the story URL to equal the repo URL exactly or the title to contain `owner/repo`, so a sub-path link is missed. A path-aware matcher finds 1 of 45. |
| F4 Reddit threads fill the digest | Last-24h digest candidates: 11 Reddit threads (scores 37-94), 4 repo items (87.8, 58.7, 52.6, 46.5), 1 video. 5 of the threads are r/ExperiencedDevs career threads. Cap of 3 per agent still lets Reddit take 3 of 5 slots; nothing reserves repo slots. Today's published digest: 3 Reddit, 1 hidden-gem repo, 1 video, 0 github-radar. |
| F5 Reddit source is degrading | Failed subreddit fetches per run 11-10..18: 0, 1, 8, 7, 9, 9, 11, 8, 10 (all `target_http_error 403`). The failing set changes daily (LocalLLaMA fails some days, succeeds others), so the 403s look transient. |
| F6 Launch posts live in small subs | `/last30days` run 2026-11-18 over 14 candidate subs: launches found in r/OpenSourceAI (4), r/mcp (2), r/ollama (1), each with 3-72 upvotes. The Reddit parser drops posts under 10 upvotes before snapshotting; the publish gate separately needs >=20 upvotes and >=1 comment (`_shared/heat.py:81`). |

## Documentation basis

| Question | Source and version/section | Rule and decision | Check |
|---|---|---|---|
| What does HN Algolia search and return? | Local: `integrations/_shared/engagement.py` already calls `hn.algolia.com/api/v1/search` with `query`, `tags=story`, `numericFilters=created_at_i>`. Official page https://hn.algolia.com/api is client-rendered (fetch returned only its title). Source README https://github.com/algolia/hn-search "Indexing Configuration": `attributesToIndex` lists title, story_text, comment_text, `url`, author, `created_at_i`. Live probe 2026-11-18 (curl): query `"ggml-org/ggml"` returned the 420-point story with its `url`. | The URL attribute is indexed, so a phrase query finds stories linking to the repo; hits carry `url`, `title`, `points`. Decision: keep the query, fix only how a hit is matched to a repo. The parameter semantics are observed, not read from official reference text. | red/green test; limit recorded |
| Do GitHub owner/repo names compare case-insensitively? | Not stated in GitHub docs I could read (renaming page covers redirects only). Secondary reports only. | Keep the repo's existing case-insensitive slug matching (`slug_mentioned` is already `re.IGNORECASE`). Open question, low impact. | none possible from docs |
| Do TinyFish per-URL failures cost the free allowance, and may I retry? | https://docs.tinyfish.ai/fetch-api/reference (read 2026-11-18): `results`/`errors` arrays, "Per-URL failures do not affect the rest of the batch"; `target_http_error` carries upstream `status`; "counted per successfully fetched URL"; 1,000 URLs/day, 150 URLs/minute, max 10 URLs per request; 429 daily limit: "do not retry this daily limit with short backoff"; 503: "retry with backoff"; client timeout >= 150 s recommended. | Retrying only the failed URLs costs no free allowance (failures not counted), stays within 10 URLs/request and 150/minute. Decision: up to 2 extra passes over failed URLs, 5 s apart; never retry a 402/429 refusal (a refused call raises as today). Client timeout in code is 120 s (docs recommend 150): raise to 150. | mock-transport tests |
| Does a lower parser floor publish junk? | `_shared/heat.py:81-107` `should_publish_heat("reddit")`: >=20 upvotes, >=1 comment, heat >=40, cooldown 7 d. | Lowering the parser floor for launch subs only feeds snapshots and baselines; the publish gate is unchanged. | test through `should_publish_heat` |
| Digest ordering rule | `.ddd/notes/perfect-daily-digest.md` score formulas; `daily_digest.py::_choose`. | Keep the score and the 50 floor; reserve up to 2 repo slots by selection, order the final list by score. | unit tests |
| Subreddit choice | `/last30days` run 2026-11-18, raw file `~/Documents/Last30Days/new-open-source-ai-agent-and-llm-developer-tools-launched-raw-v3.md` (Reddit site search was refused that run; the evidence came from listing discovery). | Add r/OpenSourceAI, r/mcp, r/ollama; r/ClaudeCode, r/LocalLLM, r/selfhosted etc. did not surface, left out. | live run after deploy |

## Plan (as implemented; deviations listed under Results)

1. `engagement._story_matches`: match a hit whose URL is on github.com (also www) and whose first two path segments equal the repo's owner/name (strip `.git`, case-insensitive); keep title match. Rejects `owner/repo-utils` and other hosts.
2. `github_source.fetch_trending_candidates_with_momentum` and `corroboration.corroborated_candidates`: one INFO line each with stage counts.
3. `reddit_source`: add three subs; `NOISE_FLOOR_BY_SUBREDDIT` (lower floor of 1 for the three launch subs, default 10); retry failed URLs in `_fetch_all_subreddits`; timeout 150 s.
4. `daily_digest._choose`: `DAILY_DIGEST_MIN_REPOS = 2`.
5. Docs: ADR 0006 consequences, `docs/reference/source-constraints-and-costs.md`, README/docs mentions of 16 subreddits, this note.

Native checks: `uv run --frozen --project integrations/github_radar pytest integrations/_shared integrations/github_radar`; `uv run --frozen --project integrations/reddit_pulse pytest integrations/reddit_pulse`; ruff; web untouched.

## Results (observed 2026-11-18, revision `6b10e45a00f8`)

**Red proofs (failing output seen before each fix):** engagement matcher 6 failed / 23 passed; corroboration count (empty output); momentum stage count (AssertionError against `git show HEAD:` source); Reddit 5 of the new tests failed (new subs, per-subreddit floor, snapshot-without-publish, retry recovery, retry cap); digest `_choose` 2 failed / 3 passed; after review, the stage-output tests failed against `logging.info`, and `test_small_launch_posts_do_not_lower_the_bar_for_their_neighbours` failed (`gate_passed=1` with low-upvote peers, `0` alone).

**Green, final revision (CI commands, `hyperadar_test` database, one suite at a time, exit 0 each):** `_shared` + `github_radar` 414 passed 2 skipped; `reddit_pulse` 40 passed; source_truth reddit 1, youtube 2, hidden_gem 1, weekly_digest 6; `youtube_trends` 32; `hidden_gems` 25; script unittests 51 OK; lockfiles current for all six projects. ruff 0.17.5 on the ten changed Python files: findings identical to the pre-change baseline (39, none new). Web app untouched, not run.

**Deviations from the plan, and why:**
- Stage counts are `print(flush=True)`, not `logging.info`: the Spec reviewer showed the agents configure no log handler, so INFO never reaches CI. The github line also splits `on_cooldown` from `published` (finding F1: 34 repos blocked only by the 14-day cooldown).
- Reddit posts under 10 upvotes are snapshotted but filtered out of the heat baseline and the gate (Spec reviewer: peers at 1-4 upvotes made a 22-upvote r/mcp post a "3x breakout", heat 52 vs 35).
- One `LAUNCH_SUBREDDITS` tuple feeds both the list and the 1-upvote floor (Standards reviewer: two lists).
- ADR 0006 amended in place; worst-case Reddit time corrected to ~1,090 s of the 1,200 s runner limit (call bound is 180 s, not the 150 s client timeout).

**Post-change comparison against sources:** reopened the diff and the files above; TinyFish fetch reference (per-URL `errors[]`, success-only allowance, 10 URLs/call, 150 s client timeout) matches `_fetch_all_subreddits` and `MONID_HTTP_TIMEOUT_SECONDS`; Algolia matching matches the ggml story shape; `heat.py` publish gate unchanged. I did not re-fetch the web docs after the edits: the plan-time reads of those pages are what I compared against.

**Independent review (two axes, 2 rounds, cap reached):** round 1 two reviewers, 10 Standards judgement findings and 5 Spec findings. Fixed: invisible logging, cooldown stage, baseline distortion, duplicated subreddit list, hard-coded retry count in a test, stale ADR status. Rebutted with grounds: matcher accepting `/issues`, `/pull`, `/commit` links (a story linking an issue of the repo is about that repo); HN "no story" vs "API failed" is the existing `fetch_hn_engagement` contract; `_choose` shape and log-wording-coupled tests kept (the printed line is the deliverable for row 3). Round 2 (one reviewer, both axes) found two items: ADR timing math (fixed) and a duplicate test comment (removed). Reviewers were separate agent sessions with fresh context.

**Unverified:** live behaviour. The retry, the three new subreddits, the HN sub-path match and the digest reservation run for real only in the next scheduled `daily-radar-refresh`. Whether 45 survivors become posts depends on live HN/Reddit data; the one measured case was `ggml-org/ggml`. `diagnosing-bugs` was not loaded: the diagnosis was done with read-only measurements instead.
