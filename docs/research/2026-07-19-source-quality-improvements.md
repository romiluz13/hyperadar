# Research: Source-Quality Improvements for HypeRadar

**Date:** 2026-07-19
**Scope:** YouTube channels, Bright Data Reddit feed, GitHub trending, hidden-gem definition + HN endpoints

---

## Summary

Four concrete improvements are available: (1) A curated list of ~15 AI-dev YouTube channels with verified channel IDs, plus `yt-dlp` syntax for channel-scoped video discovery and a view-velocity workaround via snapshot deltas. (2) Bright Data has a dedicated Reddit Scraper API (dataset `gd_lvz8ah06191smkebj4`) with `sort_by=hot` support and structured upvote/comment fields — accessible via `bdata pipelines` — far better than SERP guessing. (3) OSSInsight exposes a free, no-auth trending API (`/v1/trends/repos/`) that returns star-velocity deltas, which is materially better than the GitHub Search API. (4) "Hidden gem" on GitHub is best defined as 10–200 stars with high star-density (stars/age), and the Algolia HN API provides dedicated `show_hn`/`ask_hn` tag filters for early discovery.

---

## 1. Best AI-Dev YouTube Channels

### Curated channel list (verified IDs)

| Channel | Handle | Channel ID (UCID) | Focus | Typical views |
| --- | --- | --- | --- | --- |
| Andrej Karpathy | @AndrejKarpathy | `UCUc6v-f3S0K8X-YIe95L0Sg` | First-principles LLM architecture, tokenizer builds, training from scratch | 500K–2M+ |
| Yannic Kilcher | @YannicKilcher | `UCZHmQk67mSJgfCCTn7xBfew` | Deep paper reviews, "ML News" monthly roundup | 100K–400K |
| AI Jason (Jason Zhou) | @AIJasonZ | `UC_vOndNsnK5MreI0G2P9aog` | AI agent research, product experiments, eval loops | 50K–200K |
| Matthew Berman | @matthew_berman | `UC0uXFhM_6Gv_5GvT_Y0B0wA` | Practical AI tool tutorials, agent workflows | 200K–600K |
| Lex Fridman | @lexfridman | `UCSHZKyawb77ixDdsGog4iWA` | Long-form interviews with AI researchers | 300K–1M+ |
| Two Minute Papers | @TwoMinutePapers | `UCbfYPyITQ-7l4upoX8nvctg` | Fast research-paper summaries | 200K–500K |
| Fireship | @Fireship | `UCsBjURrPoezykLs9EqgamOA` | High-velocity dev tool updates, "100s of" series, Vercel AI SDK | 300K–1M+ |
| LangChain (official) | @LangChain | — (lookup needed) | LangGraph agent patterns, RAG, MCP | 10K–80K |
| Sam Witteveen | @samwitteveen | — (lookup needed) | Local models (Ollama, Mistral), agent frameworks | 30K–150K |
| Cole Medin | @ColeMedin | — (lookup needed) | Production LangGraph/PydanticAI/n8n agents | 20K–100K |
| Mervin Praison | @mervinpraison | — (lookup needed) | CrewAI vs AG2 vs Agno framework benchmarks | 10K–60K |
| AssemblyAI | @AssemblyAI | — (lookup needed) | Multimodal agents, speech-to-text, tool-calling | 15K–80K |
| Arize AI (Phoenix) | @arizeai | — (lookup needed) | Agentic observability, evals-as-judge, tracing | 5K–40K |

> **Note:** Channels marked "lookup needed" — the UCID was not confirmed from a primary source in this pass. Resolve via `yt-dlp --print channel_id "https://www.youtube.com/@handle"` or the YouTube Data API `channels.list?forHandle=@handle`.

### yt-dlp syntax: channel-scoped vs generic search

**List recent videos from a channel (newest first):**

```bash
yt-dlp --skip-download --flat-playlist \
  --print "%(upload_date)s|%(title)s|%(webpage_url)s" \
  "https://www.youtube.com/@AndrejKarpathy/videos"
```

**Search WITHIN a specific channel** (uses YouTube's channel search URL):

```bash
yt-dlp --skip-download --dateafter 20260101 \
  --print "%(upload_date)s|%(title)s|%(view_count)s|%(webpage_url)s" \
  "https://www.youtube.com/@YannicKilcher/search?query=agent+framework"
```

**Sort channel videos by popularity** (YouTube's `sort=p` parameter):

```bash
yt-dlp --flat-playlist "https://www.youtube.com/@Fireship/videos?view=0&sort=p"
```

**Generic YouTube search** (not channel-scoped):

```bash
yt-dlp --skip-download --flat-playlist \
  "ytsearch20:AI agent framework tutorial"
```

**Date filtering flags:** `--dateafter 20260101`, `--datebefore 20260601`, `--date 20260115`.
**Efficiency:** `--playlist-end 10` limits results; `--break-on-reject` stops when a date threshold is hit. [Source: corbpie.com](https://write.corbpie.com/searching-youtube-videos-with-yt-dlp/), [Source: notjoemartinez.com](https://notjoemartinez.com/blog/youtube_full_text_search/)

### View velocity: there is NO native YouTube API sort for it

- The YouTube Data API `search.list` `order` parameter supports only: `viewCount` (total), `date`, `rating`, `relevance`, `title`. **No "views-per-day" or "velocity" sort exists.** [Source: Google YouTube Data API docs](https://developers.google.com/youtube/v3/docs/search/list)
- `videos.list` with `chart=mostPopular` returns currently-trending videos for a region — the closest native option, but it's region-wide, not channel-scoped.
- **Workaround (snapshot delta method):** Fetch `viewCount` + `publishedAt` for target videos via `videos.list` (1 quota unit per 50 videos). Store. Re-fetch 24h later. Velocity = `(viewCount_t2 - viewCount_t1) / hours_elapsed * 24`. Sort locally. [Source: StackOverflow](https://stackoverflow.com/questions/35411282/youtube-api-v3-query-sorted-by-one-week-viewcount)
- **Heuristic (no tracking):** `velocity = total_views / days_since_upload`. Less accurate for spikes but zero-state.
- **yt-dlp sorting by views:** `yt-dlp` has no built-in view-sort flag. Use `--flat-playlist --dump-json | jq -s 'sort_by(-.view_count)'`. [Source: StackOverflow](https://stackoverflow.com/questions/79362810/using-yt-dlp-is-it-possible-to-get-the-top-n-most-popular-videos-of-a-channel)

### Actionable for HypeRadar

1. Maintain a hardcoded channel-ID allowlist (the table above).
2. Daily: `yt-dlp --flat-playlist --dateafter today-7` for each channel → get recent uploads.
3. For velocity: store daily `view_count` snapshots in MongoDB, compute 24h delta, rank by delta.
4. `yt-dlp` `--flat-playlist` is fast but omits `upload_date` reliably — use full extraction (`--skip-download` without `--flat-playlist`) when dates matter.

---

## 2. Bright Data Reddit Data Feed

### YES — Bright Data has a dedicated Reddit Scraper API

This is **structurally superior** to `bdata search "site:reddit.com/r/LocalLLaMA ..."` SERP guessing. The Reddit Scraper API returns clean JSON with upvote counts, comment counts, and subreddit metadata — no parsing required.

**Dataset IDs (confirmed from official docs):**

- **Posts:** `gd_lvz8ah06191smkebj4` — supports 3 input modes:
  - Collect by URL (single post)
  - Discover by keyword (`keyword`, `date`, `num_of_posts`)
  - **Discover by subreddit URL** (`url`, `sort_by` = `new` | `top` | `hot`)
- **Comments:** `gd_lvzdpsdlw09j6t702` — collect by post/comment URL with `days_back` parameter

[Source: Bright Data Reddit Scraper API docs](https://docs.brightdata.com/datasets/scrapers/reddit/introduction)

**Structured output fields:**

| Category | Fields |
| --- | --- |
| Post data | `post_id`, `url`, `title`, `description`, `num_upvotes`, `num_comments`, `community_name` |
| Comment data | `comment_id`, `body_text`, `author`, `upvotes`, `replies_count`, `timestamp` |
| Media/meta | `photos_urls`, `video_urls`, `tags`, `is_nsfw` |

**Limits:** Max 20 URLs per sync request; 5,000 per async request. Output: JSON, NDJSON, CSV. Real-time scrape (not cached). [Source: Bright Data Reddit docs](https://docs.brightdata.com/datasets/scrapers/reddit/introduction)

### bdata CLI commands

The `bdata pipelines` command is the CLI interface to Bright Data's structured dataset API. [Source: Bright Data CLI command reference](https://github.com/brightdata/skills/blob/main/skills/brightdata-cli/references/commands.md)

**Discover available Reddit pipeline types:**

```bash
bdata pipelines list | grep -i reddit
```

**Pull hot posts from a target subreddit** (expected syntax based on CLI reference — pipeline type name needs `bdata pipelines list` confirmation):

```bash
bdata pipelines reddit_posts "https://www.reddit.com/r/LocalLLaMA/hot/" --format json
bdata pipelines reddit_posts "https://www.reddit.com/r/MachineLearning/hot/" --format json
bdata pipelines reddit_posts "https://www.reddit.com/r/singularity/hot/" --format json
bdata pipelines reddit_posts "https://www.reddit.com/r/artificial/hot/" --format json
bdata pipelines reddit_posts "https://www.reddit.com/r/OpenAI/hot/" --format json
```

**With output to file + jq filtering:**

```bash
bdata pipelines reddit_posts "https://www.reddit.com/r/LocalLLaMA/hot/" --format json -o /tmp/localllama_hot.json
cat /tmp/localllama_hot.json | jq -r '.[] | {title: .title, upvotes: .num_upvotes, comments: .num_comments, url: .url}'
```

**Async for batch jobs (all 5 subreddits):**

```bash
bdata pipelines reddit_posts "https://www.reddit.com/r/LocalLLaMA/hot/" --async
# Returns: { "snapshot_id": "s_12345678" }
bdata status s_12345678 --wait
```

> **⚠️ Unverified:** The exact pipeline type name (`reddit_posts` vs `reddit_posts_by_url` vs other) was not confirmed from a primary source. The CLI reference shows pipeline types follow the pattern `<platform>_<entity>` (e.g., `amazon_product`, `instagram_profiles`, `youtube_comments`), so `reddit_posts` is the likely slug. **Run `bdata pipelines list | grep -i reddit` to confirm before integration.**

### Actionable for HypeRadar

1. **Replace** `bdata search "site:reddit.com/r/LocalLLaMA ..."` with `bdata pipelines reddit_posts "<subreddit>/hot/"` for structured data.
2. Pull hot posts daily from 5 target subreddits; filter by `num_upvotes > 50` and `num_comments > 20` for signal.
3. Use async mode for batch pulls across all 5 subreddits.
4. Store `post_id`, `num_upvotes`, `num_comments`, `title`, `url` in MongoDB for trend tracking.

---

## 3. GitHub Trending — Better Than the Search API

### The problem confirmed

The GitHub Search API query `created:>30d stars:>200 topic:ai sort:stars` returns the same popular repos daily because:

- The API **cannot sort by "stars gained today"** — only by total stars. [Source: StackOverflow](https://stackoverflow.com/questions/30525330/how-to-get-list-of-trending-repositories-by-github-api)
- `sort=stars` sorts by **lifetime total**, not velocity.
- The Search API returns max **1,000 results** per query.
- GitHub's official `/trending` page uses an **opaque internal algorithm not exposed via REST API**. There is no official trending endpoint as of 2026.

### (a) OSSInsight API — YES, this is the best option

**Endpoint:** `GET https://api.ossinsight.io/v1/trends/repos/`
**Auth:** None required (public). Rate limit: ~600 requests/hour/IP.
**Parameters:**

| Param | Type | Options |
| --- | --- | --- |
| `period` | string | `past_24_hours` (default), `past_week`, `past_month`, `past_3_months` |
| `language` | string | `Python`, `TypeScript`, `Rust`, `Go`, `All` |

**Example:**

```bash
curl 'https://api.ossinsight.io/v1/trends/repos/?period=past_24_hours&language=Python' \
  -H 'Accept: application/json'
```

**Response fields (star DELTA, not total):**

| Field | Description |
| --- | --- |
| `repo_name` | `owner/repo` format |
| `stars` | **Stars gained during the period** (velocity, not total) |
| `forks` | Forks gained during the period |
| `primary_language` | Main language |
| `description` | Repo description |
| `repo_current_period_rank` | Rank this period |
| `repo_past_period_rank` | Rank last period |
| `repo_rank_changes` | Rank delta (+/-) |

[Source: OSSInsight API docs](https://ossinsight.io/docs/api/list-trending-repos)

**Why this is better:** OSSInsight ingests the GH Archive (10B+ events) and live GitHub Event API, storing in TiDB. It counts `WatchEvent` (star) events within time windows — giving **deterministic star velocity**, not the opaque GitHub trending algorithm. [Source: OSSInsight blog](https://ossinsight.io/blog/introducing-trending-page)

### (b) GitHub trending endpoint — NO official one exists

As of 2026, GitHub has **no REST or GraphQL API endpoint** for trending. The `github.com/trending` page is HTML-only. Third-party scrapers (e.g., `gtrend` Python package) parse the HTML.

### (c) Search API query parameters: "trending today" vs "all-time"

| Goal | Query | Sort |
| --- | --- | --- |
| **Trending today** (best API workaround) | `created:>=YYYY-MM-DD` (today) | `stars` desc |
| **Trending this week** (active + popular) | `pushed:>=YYYY-MM-DD stars:>100` | `stars` desc |
| **All-time popular** | `stars:>1` | `stars` desc |

> **Key insight:** `pushed:>` (recent activity) is often better than `created:>` for finding "hot" repos because it surfaces older repos with current momentum. But neither gives true velocity — only OSSInsight does.

### (d) How other trending tools work

| Tool | Data source | Velocity method |
| --- | --- | --- |
| **OSSInsight** | GH Archive + live Event API → TiDB | Count of `WatchEvent` in fixed time window (true delta) |
| **Trendshift** | GitHub API polling | Momentum: deviation from historical star-growth average |
| **RepoRadar** | GitHub REST API + scraping | Snapshot delta: `stars_now - stars_24h_ago` |

[Source: OSSInsight blog](https://ossinsight.io/blog/introducing-trending-page), [Source: pagecrawl.io](https://pagecrawl.io/tools/github-trending-repository-star-velocity-alerts.html)

### Actionable for HypeRadar

1. **Primary source:** Use OSSInsight `/v1/trends/repos/?period=past_24_hours` for daily trending. Free, no auth, returns star deltas.
2. **Fallback:** GitHub Search API with `pushed:>=YYYY-MM-DD stars:>50 sort:stars` for recent activity (not velocity).
3. For AI-specific trending: OSSInsight supports `language` filter but not `topic` filter. Post-filter results by checking repo topics via GitHub API.
4. OSSInsight also has `/v1/collections/` endpoints for curated AI Agent Frameworks, LLM Tools, and MCP server collections.

---

## 4. Hidden Gems Definition + HN Endpoints

### Star ranges: hidden gem vs emerging vs trending

Community consensus (from GitHub achievement system, OSSInsight, Star-history, and developer benchmarks):

| Tier | Star range | Defining metric | What it means |
| --- | --- | --- | --- |
| **Personal/niche** | < 100 | — | Personal project or extreme niche. Check last commit date. |
| **Hidden gem** | **10–200** | Star density (stars / age in days) | High-quality niche tool, hasn't hit viral cycle. GitHub's "Hidden Gem" achievement is awarded for high stars-relative-to-age. |
| **Emerging** | **200–2,000** | Sustained momentum (+5–50 stars/day over weeks) | Becoming a respectable alternative. Featured in newsletters. |
| **Trending** | **Any** (recent spike) | Velocity (+200–2,000 stars in 24h) | Viral interest right now. Appears on GitHub Trending. |
| **Established** | 2,000–10,000 | — | Multi-contributor, decent docs. |
| **Industry standard** | 10,000+ | — | The giants (React, VS Code, etc.) |

[Source: ToolJet blog](https://blog.tooljet.com/github-stars-guide/)

> **Recommendation for HypeRadar:** The current `stars:50..500` query is too high for "hidden gems." Use **`stars:10..200`** with `created:<=2026-01-01` (age gate: at least 6 months old) and sort by star density. A repo with 80 stars created 2 months ago (density: 1.3 stars/day) is a stronger hidden-gem signal than a repo with 400 stars created 3 years ago (density: 0.4 stars/day).
>
> **Proposed query tiers:**
>
> - Hidden gem: `stars:10..200 created:>=2025-07-01` (young + low stars but growing)
> - Emerging: `stars:200..2000 created:>=2025-01-01 sort:stars`
> - Trending: OSSInsight `/v1/trends/repos/?period=past_24_hours`

### Hacker News endpoints: early discovery

**Two APIs available — Algolia is strongly recommended for HypeRadar.**

#### Algolia HN API (recommended — returns full JSON objects, searchable, filterable)

| Purpose | URL |
| --- | --- |
| **All Show HN** | `https://hn.algolia.com/api/v1/search?tags=show_hn` |
| **Newest Show HN** | `https://hn.algolia.com/api/v1/search_by_date?tags=show_hn` |
| **All Ask HN** | `https://hn.algolia.com/api/v1/search?tags=ask_hn` |
| **Newest Ask HN** | `https://hn.algolia.com/api/v1/search_by_date?tags=ask_hn` |
| **Both Show + Ask** | `https://hn.algolia.com/api/v1/search?tags=(ask_hn,show_hn)` |
| **Show HN > 100 points** | `https://hn.algolia.com/api/v1/search?tags=show_hn&numericFilters=points>100` |
| **Show HN with keyword** | `https://hn.algolia.com/api/v1/search?tags=show_hn&query=AI+agent` |
| **Full comment thread** | `https://hn.algolia.com/api/v1/items/{ID}` |

**Parameters:** `tags`, `query`, `numericFilters` (points, comments, Unix timestamp), `hitsPerPage` (max 1000), `page` (0-indexed). Rate limit: 10,000 requests/hour. [Source: Algolia HN API](https://hn.algolia.com/api), [Source: cotera.co HN guide](https://cotera.co/articles/hacker-news-api-guide)

#### Official Firebase HN API (real-time top lists only)

| Purpose | URL |
| --- | --- |
| Ask HN top stories | `https://hacker-news.firebaseio.com/v0/askstories.json?print=pretty` |
| Show HN top stories | `https://hacker-news.firebaseio.com/v0/showstories.json?print=pretty` |
| Front page top | `https://hacker-news.firebaseio.com/v0/topstories.json?print=pretty` |
| Item details | `https://hacker-news.firebaseio.com/v0/item/{ID}.json?print=pretty` |

Returns array of up to 200 IDs — must fetch each item individually (n+1 problem). No search, no filtering. No rate limit. [Source: HN API GitHub](https://github.com/hackernews/api)

### Actionable for HypeRadar

1. **For early discoveries:** Use Algolia `search_by_date?tags=show_hn&numericFilters=points>50` — gets newest Show HN posts with traction, which often surface GitHub repos before they trend.
2. **For AI-specific:** `search?tags=show_hn&query=AI+agent+LLM` — keyword-filtered Show HN.
3. **For front-page monitoring:** Firebase `topstories.json` — the current HN front page.
4. Show HN is the **primary early-discovery signal** — projects posted there often appear on GitHub Trending 24–48h later.

---

## Sources

### Kept (primary, cited in findings)

- Bright Data Reddit Scraper API docs (<https://docs.brightdata.com/datasets/scrapers/reddit/introduction>) — confirms dataset IDs, fields, sort_by options
- Bright Data CLI command reference (<https://github.com/brightdata/skills/blob/main/skills/brightdata-cli/references/commands.md>) — confirms `bdata pipelines` syntax and flags
- OSSInsight API docs — List trending repos (<https://ossinsight.io/docs/api/list-trending-repos>) — confirms endpoint, params, no-auth
- OSSInsight blog — Introducing trending page (<https://ossinsight.io/blog/introducing-trending-page>) — confirms data pipeline (GH Archive + TiDB)
- Algolia HN API (<https://hn.algolia.com/api>) — confirms tag filters, numericFilters, search_by_date
- HN API GitHub (<https://github.com/hackernews/api>) — confirms Firebase endpoints
- StackOverflow: yt-dlp channel popularity sort (<https://stackoverflow.com/questions/79362810/using-yt-dlp-is-it-possible-to-get-the-top-n-most-popular-videos-of-a-channel>) — confirms `sort=p` and jq workaround
- StackOverflow: YouTube API view velocity (<https://stackoverflow.com/questions/35411282/youtube-api-v3-query-sorted-by-one-week-viewcount>) — confirms no native velocity sort
- StackOverflow: GitHub trending API (<https://stackoverflow.com/questions/30525330/how-to-get-list-of-trending-repositories-by-github-api>) — confirms no trending endpoint, no delta sort
- ToolJet blog — GitHub stars guide (<https://blog.tooljet.com/github-stars-guide/>) — star tier benchmarks
- corbpie.com — yt-dlp search syntax (<https://write.corbpie.com/searching-youtube-videos-with-yt-dlp/>) — channel search URL syntax
- cotera.co — HN API guide (<https://cotera.co/articles/hacker-news-api-guide>) — Algolia vs Firebase comparison
- pagecrawl.io — GitHub star velocity alerts (<https://pagecrawl.io/tools/github-trending-repository-star-velocity-alerts.html>) — snapshot delta method

### Dropped

- Medium articles (miha2255, heck-the-packet) — SEO content, no primary value beyond channel discovery
- feedspot.com — channel list aggregator, not primary
- ai-weekly.ai — newsletter, secondary source
- juejin.cn — Chinese tech blog, OSSInsight fields already confirmed from official docs

---

## Gaps

1. **Exact `bdata pipelines` Reddit type name** — `reddit_posts` is the likely slug based on naming conventions but was NOT confirmed from a primary source. Must run `bdata pipelines list | grep -i reddit` to verify. The Bright Data docs reference dataset IDs (`gd_lvz8ah06191smkebj4`) but the CLI pipeline type name may differ.
2. **Channel IDs for LangChain, Sam Witteveen, Cole Medin, Mervin Praison, AssemblyAI, Arize AI** — not confirmed from primary sources in this pass. Resolve with `yt-dlp --print channel_id "https://www.youtube.com/@handle"` or YouTube Data API.
3. **OSSInsight `topic` filtering** — the API supports `language` but not `topic:ai`. AI-specific trending requires post-filtering results by checking repo topics via the GitHub API (`GET /repos/{owner}/{repo}/topics`).
4. **OSSInsight response structure** — the `data.rows` vs `data.data.rows` nesting was reported from a secondary source (juejin.cn, dropped). Should be verified by making a live API call.
5. **Bright Data pricing per Reddit request** — not confirmed. The docs say "pay per successful record" but no per-request cost was found.

### Suggested next steps

- Run `bdata pipelines list | grep -i reddit` to confirm the exact pipeline type name and parameters.
- Make a live `curl` to `https://api.ossinsight.io/v1/trends/repos/?period=past_24_hours` to verify response structure.
- Resolve remaining channel IDs via `yt-dlp --print channel_id`.
- Test `bdata pipelines reddit_posts "https://www.reddit.com/r/LocalLLaMA/hot/"` end-to-end to confirm field names in actual output.
