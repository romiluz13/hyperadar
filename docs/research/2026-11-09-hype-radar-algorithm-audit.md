# Research: HypeRadar algorithm audit vs /last30days

**Date:** 2026-11-09
**Scope:** discovery, scoring, gating, cross-source ranking, daily digest and the RomBot WhatsApp message; head-to-head with the `last30days` skill v3.26.0.
**Builds on:** `2026-07-19-source-quality-improvements.md` (source options), `docs/reference/source-constraints-and-costs.md`, ADR 0003 (momentum score), ADR 0004 (YouTube Data API). Nothing here repeats their source research; this audits what the code does today and what it produced.

## Method

| Evidence | What was done | When |
| --- | --- | --- |
| Code | Read every agent's source/agent/tracker module, `_shared/{daily_digest,write_post,momentum,corroboration,heat,engagement,evidence_copy}.py`, the workflow, `apps/web/lib/dailyDigest.ts`, RomBot `profiles/rombot/workspace/skills/hype-digest/SKILL.md`. Line numbers are against the working tree on 2026-11-09 (it has uncommitted edits in `write_post.py`, `runner.py`, `port_client.py` that belong to the parallel Port fix). | 2026-11-09 |
| MongoDB | Read-only pymongo queries on `posts`, `digests`, `agent_runs`, `signals`, `reddit_post_snapshots`, `youtube_view_snapshots`, `projects`. Window: posts since 2026-10-19 (3 weeks), digests since 2026-10-01. Scripts kept outside the repo. | 2026-11-09 ~21:00 UTC |
| Live API | `curl https://hyperadar.today/api/daily-digest` (generatedAt 2026-11-09T19:25:55Z). `gh api repos/<r>` for current stars of all 64 GitHub repos that appeared in a daily digest since Oct 1. `gh run view 42679955579 --log` (manual run of 2026-11-09 19:05Z). Spot checks of Hugging Face, OSSInsight and HN Algolia endpoints. | 2026-11-09 |
| last30days | Engine v3.26.0 on this Mac: (a) domain discovery protocol, 3 legs, domain "open source AI tools and AI coding agents", window 2026-10-10 to 2026-11-09, judged by me; (b) topic run "open source AI coding agents" with a host plan. Coverage was partial: ScrapeCreators returned HTTP 402 (no TikTok/Instagram/Reddit backfill), YouTube transcripts hit HTTP 429, free Reddit returned 0 in the discovery sweep. | 2026-11-09 ~22:00 UTC |

Legend: **[V]** verified with the cited evidence; **[I]** inferred from evidence, not directly observed.

## Headline findings

1. **Reddit has been dead for five weeks and nothing says so.** [V] Last `@reddit-pulse` post is 2026-10-06; zero `reddit_post_snapshots` since 2026-10-19; every subreddit call in today's run logged `bdata pipelines error ... Error: Token expired` (GHA run 42679955579), yet the run is recorded `ok` and `/api/daily-digest` reports `@reddit-pulse ok: true`. The Reddit leg of the corroboration gate is therefore blind too (doctor line in the same log: `reddit_corpus: FAIL — no snapshots in the last 48h`). Consequence: HypeRadar missed the Claude Fable 5.2 / Mythos 5.2 release that last30days ranked #1, although it caught Fable 5.1 via Reddit on 2026-09-02 (1,378 upvotes).
2. **Scores saturate at 100, so ranking is decided by an LLM, not by evidence.** [V] 95 of 241 `@hidden-gems` posts and 15 of 20 `@github-radar` posts in the window have `rankScore == 100`; 6 of today's 7 GitHub candidates scored 100.
3. **`@hidden-gems` is neither hidden nor gated.** [V] Median stars of its GitHub posts: 4,018; 133 of 232 had >2,000 stars (Ponytail 150k, Caveman 109k, Agent-Reach 91k). It scores the same shared `signals` pool as `@github-radar`, is exempt from corroboration, and 155 of its 241 posts (64%) re-posted a URL already published before.
4. **The Show HN leg posts all-time classics, not new launches.** [V] `hidden_gems/source.py:67-73` queries Algolia `search` with `tags=show_hn&points>50` and no date filter; on 2026-10-22 it published nine posts such as "Show HN: This up votes itself" (2012) and "Redbean" at rankScore 70. Verified live: the same query today returns a 2012 story first.
5. **The digest is capped by agent, not by quality.** [V] Max 2 per agent; with only GitHub and YouTube publishing today, 4 items is the structural ceiling, so two 40-point YouTube videos ("Anthropic just built it...") displaced four 100-point GitHub repos.
6. **The corroboration gate is real but weak, and it works.** [V] A 1-3 point HN submission counts as the second source (e.g. `pullboard`, 3 HN points). Even so, corroborated `@github-radar` digest picks grew ≥2x in stars after the pick 41% of the time (9/22) vs 22% (9/40) for un-gated `@hidden-gems`.
7. **Runs sit at the timeout ceiling.** [V] Successful `@hidden-gems` runs take median 877 s, max 1,182 s against a 1,200 s limit (`runner.py:25`); `@github-radar` 821 s / 1,140 s. Both score all 2,017 tracked repos every run, of which 1,824 have no snapshot in the last 7 days.
8. **last30days is not a better hype radar for this domain; it is a better listener.** [V] Its discovery sweep nominated 15 items, 14 of them X posts from the same day, 7 of which I judged junk (crypto promo, roadmap spam, news roundups). None of its ranked picks was a GitHub star breakout. It does win on breadth (X, Polymarket, Techmeme, Bluesky), on model-release events and on quotes. HypeRadar's edge is longitudinal star history and outcome-checkable picks; its weakness is broken or missing listening channels.

## Q1. Per agent

| Agent | Sources / APIs | Signals computed | Score and gate | Dedup / cooldown | LLM | Runtime (ok runs, Oct 19-Nov 9) |
| --- | --- | --- | --- | --- | --- | --- |
| `@github-radar` | GitHub Search (rotating band, topics ai/llm/agent, created ≤30-90 d, `github_radar/tracker.py:34-39,101-150`); github.com/trending daily+weekly HTML scrape, no AI filter (`tracker.py:49-90`); GitHub star-history backfill (`_shared/star_history.py`); HN Algolia per-repo lookup (`_shared/engagement.py:105`); Reddit corpus or live search for corroboration | 7-day decayed velocity, week-over-week acceleration, relative growth, fork/star depth, consistency, viral bonus, novelty bonus (`_shared/momentum.py:146-224`) + HN engagement boost 0-15 (`engagement.py:43`) | Pool-scaled 80th percentile threshold, floor 30 (`momentum.py:251-273`); velocity>0, acceleration>0, fork/star ≥0.02, monotonic history, 14-day cross-agent cooldown (`momentum.py:276-306`); then 2-source corroboration (`_shared/corroboration.py`); LLM told to skip momentum <40 | `write_post` one post per agent+URL+day (`write_post.py:684-697`); 14-day cross-agent cooldown at fetch and write (`github_radar/agent.py:232-238`) | Deep Agents on Grove (`FW-DeepSeek-V4.1-Flash` per commit 6fce7b7), temperature 0.7 (`agent.py:314`); LLM picks posts and verdict | median 821 s, max 1,140 s; 14 ok of 39 runs; 0-7 posts per run |
| `@hidden-gems` | Same shared `signals` pool (tracker band 10..500 stars, `hidden_gems/tracker.py`), HN Algolia Show HN (`hidden_gems/source.py:58-100`) | Same momentum score; HN boost post-gate | Same gate as above, **no corroboration** (`corroboration.py` docstring: "@hidden-gems is exempt by design"); HN leg: `min(35 + points/10, 70)` | Same | Deep Agents, 29-49 write calls per run | median 877 s, max 1,182 s; 12 ok of 41; 15-44 posts per ok run |
| `@reddit-pulse` | Bright Data `bdata pipelines reddit_posts` on 16 `/rising/` subreddit URLs (`reddit_pulse/reddit_source.py:23-40`) | Upvotes, comments, age, upvotes/hour, heat vs same-run baseline (`_shared/heat.py`) | heat ≥40; ≥3x baseline when confidence high; ≥20 upvotes, ≥1 comment; 7-day cooldown (`heat.py:57-108`) | Snapshot rows per run | Deep Agents | median 15 s (every subreddit fails fast); **0 posts in 12 ok runs** |
| `@youtube-trends` | YouTube Data API v3, 23-channel allowlist, 14-day lookback (`youtube_trends/source.py:31-59`) | Views, view velocity from snapshots, channel-relative velocity, heat | heat gate, 14-day cooldown | `youtube_view_snapshots` | Deep Agents | median 34 s; 6 posts in 3 weeks, all with body "upload-age view velocity was not measured" |
| `@community-radar` | RomBot community-ask API, one fixed question about the top 10 topics of the last 2 weeks (`community_ask/source.py:12-21`) | Contributor estimate | none | URL is a topic slug | RomBot LLM + Grove | median 407 s; exactly 10 posts per ok run, the same 10 URLs every day (each URL posted 10 times since Oct 19) |
| `@weekly-digest` | MongoDB only | none | none | weekId | Grove | not in the daily GHA matrix; the per-agent workflow `run-hyperadar-agent.yml` last ran 2026-10-09, so the daily weekly-digest posts of Oct 19-28 came from the local launchd job [I: by elimination]; `hyperadar://digest/2026-W42` posted six times |

LLM cost per run: **could not verify.** `agent_runs` records `fetchCalls`, `writeCalls`, `postsWritten`, `doctor` but no token counts or cost [V: keys of `agent_runs` docs]. Every `write_post` also calls Voyage `voyage-4-large` for an embedding (`_shared/embeddings.py:12,52`) and Port upserts.

## Q2. Cross-source, ranking and digest selection

**Multi-source confirmation** [V] (`write_post.py:297-300`): `boost = min((distinct agents that posted the same project.url) - 1, 2) * 10`. It only fires on an exact URL match, so a YouTube video, a Reddit thread and a GitHub repo about the same project never confirm each other. In the window, the only cross-agent URLs were two repos posted by both `@github-radar` and `@hidden-gems`, which read the same `signals` pool: the "second source" was the same data.

**Corroboration gate** [V] (`corroboration.py`, `github_radar/agent.py:93`): `@github-radar` keeps a candidate only if an HN story in 14 days mentions the repo or the Reddit corpus does. With the corpus empty and live Reddit returning 403, HN is the only leg. Any HN story qualifies; there is no points floor.

**rankScore** [V]: `rank_score = momentumScore` (`github_radar/agent.py:275`), capped at 100 after the HN boost. The velocity component saturates at 50 stars/week (`momentum.py:183-187`) and acceleration at +25 stars (`momentum.py:190-192`), so a repo gaining 10,000 stars/week and one gaining 60 get the same 60 points from those two parts.

**How items reach the daily digest** [V] (`_shared/daily_digest.py`):
1. `generate-digest` runs after the agent matrix (`.github/workflows/daily-radar-refresh.yml`, `needs: [run-all-agents]`, `if: always()`).
2. Pool: posts with `postedAt` in the last 24 h, from `@github-radar`, `@youtube-trends`, `@hidden-gems`, `@reddit-pulse`, `portSyncStatus: synced`, contract v2 (`daily_digest.py:27-31,206-214`); top 50 by `rankScore` (`:309`).
3. One Grove call: "Pick the top 5 most interesting/hyped items ... Max 2 from the same source", returns `{id, blurb}` and an optional `what` (`:217-259`).
4. Order = the order the LLM returned; the per-agent cap of 2 is re-enforced; if fewer than 5, backfill by `rankScore` order (`:316-396`).
5. If the pool is empty, the function returns `None` and **writes nothing** (`:311-312`); the API keeps serving the newest older digest (`apps/web/lib/dailyDigest.ts:88-93`) with `degraded: true` after 48 h.
6. No memory of previous digests: 23 URLs appeared in more than one daily digest since Oct 1 [V]; the 2026-10-27 digest was exactly the 2026-10-12 pair (`gods-eye-view`, `replica-skill`) re-posted after their 14-day cooldown.

Publication depends on Port: a post enters the digest only after its Port twin syncs (`portSyncStatus: synced`). [I] That coupling is why a Port auth failure froze the digest from Oct 29 to Nov 9 instead of degrading it (Port itself was out of scope).

**Scheduling** [V]: the cron is 00:23 UTC, but GHA created the scheduled runs at 06:22 Z (Nov 8) and 06:55 Z (Nov 9). With agents capped at 20 min, the digest still lands before RomBot's fetch (10:30 UTC in Israel winter time), with roughly 3 h of slack [I: from run creation times plus the timeout cap]. A local launchd job (`com.hyperadar.daily-run`, `scripts/daily_run.sh`) also runs five agents daily on the Mac (today: "0 succeeded, 5 failed").

## Q3. Coverage gaps vs last30days

| Channel | last30days | HypeRadar | Addable? |
| --- | --- | --- | --- |
| GitHub stars over time | issues/PR search only | **Yes**: daily snapshots + star-history backfill, 70,728 signal docs | HypeRadar's moat [V] |
| Reddit | free JSON + ScrapeCreators | Bright Data, broken since ~Oct 7 | Fix the Bright Data token (S). Official Reddit OAuth API is the fallback [I: ToS and rate limits not re-checked here] |
| Hacker News | discovery source (front page, search) | per-repo lookup + broken Show HN leg | Yes, free: Algolia `search_by_date` with `created_at_i>` and points filter [V: Algolia endpoint live] |
| X / Twitter | cookies (`bird`), xAI key or X API bearer | none | Hard. Cookie scraping is a ToS risk for a public product; the X API is paid. [I] Use xAI/Grok search or skip |
| YouTube | search + transcripts + comments | 23-channel allowlist, views only | Already present; value is low as a standalone item (see Q4) |
| Polymarket | yes | none | Free gamma API, but low relevance to repo hype [I] |
| TikTok / Instagram | ScrapeCreators | none | Paid third party, low dev-tool signal [I] |
| Bluesky | app password | none | Free and ToS-friendly, small dev audience [I] |
| Techmeme, Digg, arXiv | CLIs | arXiv tried and reverted (commit 87461a8: edge 406s Python clients) | Techmeme RSS is free [I] |
| Hugging Face trending | no | none | Yes: `GET https://huggingface.co/api/models?sort=trendingScore` returns `trendingScore`, likes, downloads without a key [V: live call today, top item `speridlabs/idol` 838] |
| npm / PyPI downloads | no | none | Yes, free (`api.npmjs.org/downloads`, pypistats) as an adoption check on repos that publish packages [I] |
| Product Hunt | no | none | GraphQL API needs a token, commercial terms unclear [I] |
| OSSInsight trending | no | coded in the legacy path only (`github_source.py:90`) | **Returned 0 rows today** [V], so it is dead weight |
| RomBot community (~4k members) | no | `@community-radar`, excluded from the digest | Unique signal nobody else has; today it is wasted (Q4) |

Structural gap [V]: discovery only finds repos created in the last 30-90 days (`tracker.py` bands) or on the GitHub trending page. An older repo with a new moment (e.g. `Muvon/octomind`, created 2025-06-01, 56 HN points on Nov 3) can only enter through the trending page.

## Q4. Quality: noise and missed hype

**Outcome check on all 62 GitHub digest picks from Oct 1-29** [V: Mongo `signalValue` at pick vs `gh api` stars today]:

| Metric | `@github-radar` (corroborated) | `@hidden-gems` (not corroborated) |
| --- | --- | --- |
| Picks | 22 | 40 |
| Grew ≥2x since pick | 9 (41%) | 9 (22%) |

Best calls: `morluto/rea` 6,829 → 446,500 (65x, hidden-gems, Oct 6); `nano-muse/nanoMuse` 270 → 3,358 (github-radar, Oct 8); `boykopovar/AnyPS5` 4,399 → 52,580; `SoMarkAI/OXR` 378 → 3,721; `ranxianglei/billion-context` 801 → 5,701; `storytold/artcraft` 5,999 → 40,213; `JetBrains/thinkrail` 1,694 → 8,043. These are real early calls that last30days did not make.

**False positives** [V for the items, I for the "off-topic" label]:
- Off-topic for an AI radar, about 13 of 62 picks: `HunxByts/GhostTrack` ("track location or mobile number"), `jbiaojerry/ebook-treasure-chest` (ebook download links), `liquidslr/system-design-notes`, `cs341-illinois/coursebook`, `Open-Dev-Society/OpenStock`, `asciimoo/hister`, `derv82/wifit3`, `DuarteSantos8/openGym`, `xtool-org/xtool`, `M-Abozaid/esp32-c3-adblock`, `Crosstalk-Solutions/project-nomad`, `pablostanley/yoinks`, `boykopovar/AnyPS5`. Cause [V]: the GitHub trending scrape has no AI filter (`tracker.py:53-90`) and feeds the shared pool, and `@hidden-gems` publishes from it without corroboration.
- Not hidden: 133 of 232 `@hidden-gems` repo posts had >2,000 stars.
- Recycled: Caveman posted Oct 3 and Oct 18, Ponytail Oct 2 and Oct 16; 64% of hidden-gems posts in the window repeat an earlier URL.
- HN classics (finding 4). Nine on Oct 22, none AI.
- Hype-flavoured YouTube: all 6 video posts were titles like "they're shutting it DOWN" and "it's WORSE than we thought..." with raw view counts and "velocity was not measured" bodies.
- `@github-radar` evidence copy is wrong on the momentum path [V]: every post body says "AVG 0★/wk since creation ... recent growth was not independently measured" because the momentum path passes `avgStarsPerWeekSinceCreation: 0.0` (`github_radar/agent.py:178`) even though velocity is known.

**Missed hype** [V]:
- Claude Fable 5.2 / Mythos 5.2 release: last30days #1 (HN 448 points, X 16k likes, Polymarket markets). HypeRadar: nothing after Reddit died. It is a model release, not a repo, so even a healthy GitHub pipeline would miss it.
- `claude-watch`, Cloudroom, DBDelve, FrontierCUA, DevSpace, Octomind, Traccia, klark, AgentMon: zero posts, signals or projects in Mongo. Most are low-engagement X/HN launches; Octomind (637 stars) and DBDelve (62 HN points) are the credible misses.
- Old-repo, new-moment class (Octomind) is invisible by design (Q3).

**Today's digest has 4 items. Right number?** [V/I] Four is the structural ceiling of today's pool (7 GitHub + 2 YouTube, max 2 per agent), not a quality decision. The GitHub pool had stronger material (`astryx` 19.5k stars, `cindy` 7.6k, `OpenMausBot` 19.5k, `pullboard`) than the two YouTube clickbait videos that filled slots 3 and 4. [I] Three strong items beat four with two weak ones; on a day like this the right message is 2-3 items.

## Q5. Efficiency

| Waste | Evidence | Effect |
| --- | --- | --- |
| Whole-pool rescoring | Both momentum paths loop over every projectId with ≥7 snapshots: 2,017 repos, each with a history `find` + `count_documents` + later `get_last_published_days` (`github_source.py:405-465`, `hidden_gems/source.py:157-230`) [V] | ~6,000 sequential round trips per agent per run, run twice daily on the same data [I: round-trip count from code, not measured] |
| Scoring stale repos | 1,824 of 2,017 have no snapshot in 7+ days [V] | The "7-day" velocity of these uses old snapshots; `_stars_at` indexes by position, not date (`momentum.py:36-40`) [V], so gaps silently stretch the window |
| Two trackers, one pool | github-radar and hidden-gems each run `track_daily_snapshots` and score the same `signals` set [V] | Duplicate work and duplicate posts (2 cross-agent URLs, 155 recycled) |
| Per-post side effects | Each of 29-49 hidden-gems writes embeds with Voyage, upserts Port, takes a reconcile lease [V: `write_post.py`] | Timeout pressure: median 877 s vs 1,200 s cap; 8 timeouts in 3 weeks |
| Silent dead sources | reddit-pulse spends a run, a Grove doctor call and a Deep Agents loop on 16 expired-token failures [V] | Cost with no output, health shows green |
| Dead OSSInsight code | API returned 0 rows today [V] | Legacy path silently falls to Search API |
| Duplicate scheduler | launchd `com.hyperadar.daily-run` still runs 5 agents locally [V] | Double API/LLM spend when both succeed; races the cooldown |
| LLM on deterministic choices | Verdict and "skip momentum <40" are LLM decisions at temperature 0.7 [V] | Non-reproducible verdicts; cost per post [I] |

## Q6. The WhatsApp message

What RomBot does [V] (`profiles/rombot/workspace/skills/hype-digest/SKILL.md`): fetch the digest at 12:30 Asia/Jerusalem, take the first 4 items, one Hebrew line per item from `what`, one stat from `signalMetric` (stars/HN points/upvotes/views), weekly growth only for stars with a 7-8 day window, never the blurb, never the verdict, 650-character cap, RTL marks, tags for stale date and missing sources.

What today's data produces [I: rendered by hand from the rules, not observed in WhatsApp; RTL marks, blank lines between blocks and the two footer lines omitted, URLs shortened]:

```
🔥 הייפ-רדאר יומי — auto-ontology של NVIDIA מזנק ל-11K כוכבים · חלק מהמקורות חסרים
• 📦 *auto-ontology* — מסגרת Python לבניית שכבות היסק סמנטיות מבוססות גרף
⭐ 11K · 9.9K חדשים בשבוע · 🔗 github.com/NVIDIA/auto-ontology
• 📦 *Flide* — מנוע הסקה מהיר במיוחד למודלי החלטה
⭐ 255 · 247 חדשים בשבוע · 🔗 github.com/OmniJev/Flide
• 🎥 *OpenAI's Astra Just Opened The Black Box…*
👁 210K צפיות · 🔗 youtube.com/...
• 🎥 *Anthropic just built it...*
👁 190K צפיות · 🔗 youtube.com/...
```

Assessment:
- **The strongest evidence never reaches the reader.** [V] auto-ontology's case is "292 HN points, 139 comments" (in `blurb`), but the stat line can only show `signalMetric`, which is `github_stars` for every repo. The blurb, the only "why now", is discarded by design.
- **Two of four lines carry no information.** [V] Video items have `what: null` by design (`daily_digest.py:165-166`), so the reader gets an English clickbait title. "Anthropic just built it..." says nothing.
- **Growth on a brand-new repo is not breakout.** [V] Flide has 255 stars and a 247 weekly gain: almost all its stars are new, so "247 new this week" is trivially true.
- **Text quality leaks from upstream.** [V] `what` for Flide begins with "🔥🔥" (raw GitHub About line); auto-ontology's `what` is cut mid-sentence at 140 chars ("It enables deterministic query"). The skill asks the model to translate the defining sentence, which mitigates it.
- **Source health is half right.** [V] The "חלק מהמקורות חסרים" tag fires for hidden-gems today, but not for Reddit, whose agent reports `ok: true` while producing nothing for five weeks.
- **Stale days still send.** [V] With no digest written, the API serves the last one and RomBot adds "נתונים מ-DD.MM"; on Oct 30 - Nov 8 that meant a 2026-10-29 digest. [I] A stale digest is better as `EMPTY` plus a one-line notice than as yesterday's picks.
- Hebrew/RTL: the U+200F rule, rounding and length rules are sound [V by reading]. Not verified on a device.

## Side-by-side: same domain, same window (2026-10-10 to 2026-11-09)

| # | last30days discovery (domain "open source AI tools and AI coding agents") | last30days topic run ("open source AI coding agents") | HypeRadar digest picks in the domain, same window |
| --- | --- | --- | --- |
| 1 | Claude Fable 5.2 + Mythos 5.2 release: HN 448 pts, X 16.4k likes, Polymarket, 7 sources | X post on code knowledge graphs, 4 likes | `stablyai/orca` (fleet of coding agents) 95.6k → 120k stars |
| 2 | claude-watch (agent learns from video): evidence drifted into generic "Learn Claude Code" videos | klark (agent-to-agent messaging), 1 like | `pingdotgg/t3code` 26.8k → 53.9k |
| 3 | Cloudroom (cloud coding agents desktop): YouTube 150k views | AgentMon Live press post, 0 likes | `yetone/magpie` (any model in any agent) 13.7k → 33.1k |
| 4 | Open-source cyber attack globe: HN 413 pts (off-domain) | Better Stack video on NVIDIA OpenShell, 52.9k views | `NVIDIA/OpenShell` 15.4k → 21.3k (also in 3 digests) |
| 5 | DBDelve (Rust DB client, not AI): HN 62 pts | Show HN Traccia, 5 pts | `JetBrains/thinkrail` (agentic IDE) 1.7k → 8.0k |
| 6 | FrontierCUA benchmark: 44 X likes | DAMS shared memory server (r/ClaudeCode, 1 pt) | `cloudflare/security-audit-skill` 37.8k → 67.9k |
| 7 | - | Show HN Shield KYA, 3 pts | `Jakeschincariol/replica-skill` 2.0k → 8.6k; `Nutlope/hallmark` 33.7k → 51.7k |
| 8 | - | Octomind (HN 56 pts, 39 comments; repo 637 stars) | `ranxianglei/billion-context` 0.8k → 5.7k; `akitaonrails/ai-memory` 12.6k → 15.5k |

Notes [V]: last30days nominations were 14 X posts and 1 HN post, all dated 2026-11-09 (recency-weighted sweep), 7 judged junk. Its "community voice" for two topics was a code-review bot comment. Overlap between the two tools: NVIDIA/OpenShell and t3code only. last30days surfaced the biggest event of the month (Fable 5.2); HypeRadar surfaced the repos that actually grew.

**Headline:** For this domain HypeRadar finds better *things* (repos that later 2-65x) and last30days finds better *moments* (model releases, HN/X conversations, quotes). Neither is the world's best radar alone; the winning design is HypeRadar's star history plus a working listening layer (Reddit, HN, HF, optional X) that confirms entities across sources.

## Ranked improvements

Impact: what the user sees in the daily message. Effort: S ≤ 1 day, M ≤ 1 week, L > 1 week.

### 1. Make a dead source fail loudly, and fix the Reddit token
- **Impact:** High. Restores Reddit discovery, the Reddit corroboration leg and model-release coverage; stops green health on a dead source.
- **Effort:** S. **Risk:** Low.
- **Change:** a source run where every fetch errors (or 0 items fetched for N runs) records `ok: false`; the Bright Data token is rotated (secret only). Doctor's existing `reddit_corpus: FAIL` becomes a run failure for `@reddit-pulse`.
- **Files:** `integrations/reddit_pulse/reddit_source.py:188-265`, `integrations/_shared/runner.py` (quiet-day logic around `:110,206`), `integrations/_shared/doctor.py`.
- **Verify:** fixture where `bdata` prints `Error: Token expired` → run recorded `ok: false`; `/api/daily-digest` shows `@reddit-pulse ok:false`; after rotation, `reddit_post_snapshots` gets rows today.

### 2. Rank on uncapped evidence, then let the LLM write, not choose
- **Impact:** High. Ends the 100-point tie; ordering becomes explainable and stable.
- **Effort:** M. **Risk:** Medium (changes what tops the digest).
- **Change:** a digest score from raw signals: log-scaled 7-day star gain normalised by days actually spanned, relative growth, HN points/comments, Reddit upvotes, HF trendingScore, number of independent sources, with a penalty for `inflated` and for prior digest appearances. Sort by it; the LLM only writes the blurb and `what`.
- **Files:** `integrations/_shared/daily_digest.py:296-396`, `integrations/_shared/momentum.py:146-224` (expose raw parts), tests in `test_daily_digest.py`.
- **Verify:** replay Oct 1-29 posts offline; ≥2x outcome rate of the top 3 per day beats today's 41%/22%; no ties at the top on 2026-11-09 data.

### 3. Re-scope `@hidden-gems`: low-star only, deduped, gated
- **Impact:** High. Removes most off-topic and 100k-star "gems", the recycling, and the timeouts.
- **Effort:** M. **Risk:** Medium.
- **Change:** score only repos under a star ceiling (e.g. ≤2,000) that its own tracker found; one lifetime publication per URL unless a new acceleration event; require an AI relevance check on trending-page entries; cap posts per run (e.g. 5).
- **Files:** `integrations/hidden_gems/source.py:157-278`, `integrations/hidden_gems/tracker.py`, `integrations/github_radar/tracker.py:53-90` (AI filter on trending), `integrations/_shared/momentum.py:276-306`.
- **Verify:** replay window: hidden-gems median stars < 2,000, recycled share < 10%, run time < 300 s.

### 4. Fix the Show HN leg and make HN a discovery source
- **Impact:** High. Fresh launches 24-48 h before GitHub trending; kills the 2012 posts.
- **Effort:** S. **Risk:** Low.
- **Change:** `search_by_date` with `numericFilters=created_at_i>{now-72h},points>30`, plus front-page stories that link to github.com; AI relevance filter; HN items feed the tracker pool so they get star history.
- **Files:** `integrations/hidden_gems/source.py:58-100`, `integrations/_shared/engagement.py`.
- **Verify:** unit test asserts the request carries a `created_at_i` filter; live dry run returns only stories < 72 h old.

### 5. Select the digest by quality, not per-agent quota; remember what was sent
- **Impact:** High on the message: no clickbait filler, no repeats.
- **Effort:** S. **Risk:** Low.
- **Change:** minimum score for inclusion; 2-5 items, fewer on thin days; per-agent cap only as a tiebreak; exclude URLs featured in the last 30 daily digests unless a new milestone; YouTube items only when they are about a tracked project (attach as evidence) rather than standalone.
- **Files:** `integrations/_shared/daily_digest.py:27-35,206-214,316-396`.
- **Verify:** on 2026-11-09 data the digest is GitHub-led with no "Anthropic just built it..."; on Oct 1-29 replay zero URL repeats inside 30 days.

### 6. Put the "why now" in the message
- **Impact:** High on reader value: the reason something is hot is the story.
- **Effort:** S. **Risk:** Low.
- **Change:** digest items carry a structured `evidence` list (e.g. `{source: "hn", points: 292, comments: 139}`, `{source: "reddit", upvotes}`); RomBot shows one secondary stat (`292 נק׳ ב-HN`) when present and suppresses weekly growth when gain/stars > 0.8 (new repo). Strip emoji and cut `what` at a sentence boundary.
- **Files:** `integrations/_shared/daily_digest.py:48-68,333-354`, `apps/web/lib/dailyDigest.ts:6-27`, `RomBot-ClawMongo/profiles/rombot/workspace/skills/hype-digest/SKILL.md`.
- **Verify:** render today's JSON: auto-ontology line shows HN points; Flide shows no growth part; output ≤650 chars.

### 7. Cut the run cost: incremental pool, one tracker pass
- **Impact:** Medium-High. Ends the 1,200 s timeouts and stale-window velocity.
- **Effort:** M. **Risk:** Low.
- **Change:** one aggregation that returns, per repo, the last 15 daily snapshots for repos snapshotted in the last 48 h only; snapshot every tracked repo that is still "live" daily (not just today's band) so velocity windows are real days; compute velocity by date, not index; drop OSSInsight; retire the launchd duplicate.
- **Files:** `integrations/github_radar/github_source.py:405-465`, `integrations/hidden_gems/source.py:157-230`, `integrations/_shared/momentum.py:36-90`, `integrations/github_radar/tracker.py:153-220`, `scripts/daily_run.sh` + local plist.
- **Verify:** `agent_runs` ok-run duration median < 300 s for both agents over a week; a gap-fixture test proves a 3-snapshot/14-day history is not treated as 7 days.

### 8. Entity-level corroboration across sources
- **Impact:** Medium-High. Makes multi-source boost meaningful; Reddit/YouTube/HN/community mentions confirm repos.
- **Effort:** M. **Risk:** Medium (false matches).
- **Change:** resolve each non-repo item to a canonical entity (GitHub URL in text, `owner/repo` slug, HF model id); boost by distinct *sources*, not agents; require an HN floor (e.g. ≥20 points) or ≥2 sources for "corroborated".
- **Files:** `integrations/_shared/write_post.py:257-345`, `integrations/_shared/corroboration.py`, `integrations/_shared/engagement.py`.
- **Verify:** test that a Reddit thread linking `github.com/x/y` raises `y`'s source count; `pullboard` (3 HN pts) no longer passes.

### 9. Decouple publication from Port
- **Impact:** Medium. A Port outage degrades the twin, not the feed and the digest.
- **Effort:** M. **Risk:** Medium (touches the Port showcase; coordinate with the parallel Port fix).
- **Change:** digest eligibility on MongoDB publication state; Port sync becomes asynchronous repair.
- **Files:** `integrations/_shared/write_post.py:487-512,623-792`, `integrations/_shared/daily_digest.py:206-214`.
- **Verify:** fixture with Port auth failing still yields a digest with `degraded: true`.

### 10. Add Hugging Face trending and an "old repo, new moment" lane
- **Impact:** Medium. Covers model releases and resurging repos.
- **Effort:** M. **Risk:** Low.
- **Change:** HF `models?sort=trendingScore` (and spaces) as a source with daily snapshots; let HN/Reddit mentions add any repo (regardless of age) to the tracker.
- **Files:** new `integrations/_shared/hf_source.py` or a new agent; `integrations/github_radar/tracker.py`.
- **Verify:** a week of HF snapshots; at least one HF item in the digest candidate pool per day.

### 11. Turn `@community-radar` into a signal, not a feed
- **Impact:** Medium. The ~4k-member community is a channel last30days cannot reach.
- **Effort:** S-M. **Risk:** Low.
- **Change:** ask for named tools/repos with message counts instead of 10 fixed topics; use matches as corroboration and a "discussed in the community" line in the message; stop re-posting identical topic slugs daily.
- **Files:** `integrations/community_ask/source.py:12-21`, `integrations/community_ask/agent.py`, `integrations/_shared/corroboration.py`.
- **Verify:** distinct community URLs per day > 0 new; corroboration count includes community.

### 12. Deterministic verdicts and fixed evidence copy
- **Impact:** Medium on trust. **Effort:** S. **Risk:** Low.
- **Change:** compute verdict from thresholds; temperature 0 or no LLM for selection; pass real velocity to `github_evidence_copy` so bodies stop saying "AVG 0★/wk".
- **Files:** `integrations/github_radar/agent.py:168-190,239-248,314`, `integrations/_shared/evidence_copy.py:8-19`.
- **Verify:** two runs on the same fixture give identical verdicts; no "AVG 0★/wk" in new post bodies.

### 13. When the digest is stale, say so instead of resending
- **Impact:** Medium on trust. **Effort:** S. **Risk:** Low.
- **Change:** RomBot outputs `EMPTY` (or a one-line "radar is down since DD.MM") when `date` is more than one day old.
- **Files:** `RomBot-ClawMongo/profiles/rombot/workspace/skills/hype-digest/SKILL.md` step 1/4.
- **Verify:** fixture JSON dated 3 days ago yields the notice, not items.

### Not recommended now
- X via cookies: ToS risk for a public, branded product [I]. Revisit with xAI search if X becomes essential.
- TikTok/Instagram/Polymarket: low signal for developer-tool hype in this data [I].

## Could not verify

- LLM token use and cost per run: no telemetry is stored.
- Exact cause of today's `@hidden-gems` timeout: out of scope; the evidence above (run times near the cap, whole-pool rescoring, 29-49 writes) is algorithmic context only.
- The rendered WhatsApp message on a device; the Hebrew lines above are my rendering of the rules.
- Port's role in the Oct 29-Nov 9 freeze beyond the 401 errors in `agent_runs` (Port not investigated, per brief).
- last30days' full potential: ScrapeCreators credits were exhausted (402), YouTube transcripts were rate-limited (429) and free Reddit returned 0 in the sweep, so its Reddit/TikTok/Instagram lanes were underrepresented.
- "Off-topic" classification of the 13 digest picks is my judgement from their GitHub descriptions.
- Reddit official API terms and current pricing were not re-checked.

## Reproduce

- Mongo: read-only pymongo, `set -a; . ./.env; set +a` inside the command; collections and filters as named in each finding (posts `postedAt >= 2026-10-19`; digests `digestType: daily, createdAt >= 2026-10-01`; `agent_runs` duration = `finishedAt` minus the timestamp in `threadId`).
- Outcomes: for each GitHub URL in a daily digest since Oct 1, compare the item's `signalValue` with `gh api repos/<owner>/<repo> --jq .stargazers_count`.
- last30days: `last30days.py --discover "open source AI tools and AI coding agents" --nominate-only`, then `--judgments`, then `--finalize --angles`; and `last30days.py "open source AI coding agents" --plan <plan> --subreddits=ClaudeAI,ChatGPTCoding,LocalLLaMA,cursor,ClaudeCode,OpenAI --emit=compact`.
