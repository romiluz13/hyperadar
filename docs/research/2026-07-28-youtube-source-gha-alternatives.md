# Research: Alternatives to yt-dlp for YouTube Trending Data on GHA Datacenter IP

**Date:** 2026-07-28
**Context:** yt-dlp returns 0 results on GitHub Actions Ubuntu runners because YouTube's anti-bot blocks datacenter IPs. It only works from a residential IP (the dev's Mac). The HypeRadar repo already uses Bright Data (`bdata`) for the Reddit agent. This research evaluates alternatives for the YouTube source (`integrations/youtube_trends/source.py`) that scans 23 curated AI-dev YouTube channels' `/videos` pages for recent uploads (last 14 days), extracting view count, channel, and subscriber count.

---

## Summary

The YouTube Data API v3 is the strongest single replacement — it's free, works from any IP (including GHA datacenter), provides all required fields (views, subscribers, dates, title), and costs ~2,302 quota units/day (well within the 10,000-unit default). A viable fallback is RSS feeds (for video URLs + view counts) combined with `bdata pipelines youtube_profiles` (for subscriber counts), which stays within the existing Bright Data infrastructure. yt-dlp workarounds (Cloudflare WARP, PO tokens) are too fragile for a production daily cron.

---

## Findings

### 1. Bright Data `bdata pipelines youtube_videos` — VIABLE-WITH-CAVEATS

**What it is:** The `bdata pipelines` CLI command triggers an async collection job via the Bright Data `/datasets/v3/trigger` endpoint, polls until ready, and returns structured JSON. The `youtube_videos` pipeline type maps to the YouTube Videos dataset (`gd_lk56epmy2i5g7lzu0k`), which accepts a video URL (`youtube.com/watch?v={video_id}`) and returns: `title`, `url`, `video_id`, `youtuber`, `views`, `likes`, `num_comments`, `date_posted`, `subscribers`, `description`, `tags`, `channel_url`, `preview_image`, `quality`, `transcript`, `is_sponsored`, `is_age_restricted`. ([Bright Data CLI pipelines reference](https://github.com/brightdata/skills/blob/main/skills/brightdata-cli/references/pipelines.md), [Bright Data YouTube Videos collect-by-url](https://docs.brightdata.com/api-reference/scrapers/social-media-apis/youtube-videos-collect-by-url))

**The problem:** The `youtube_videos` pipeline type takes a **video URL** (`youtube.com/watch?v=...`), not a channel URL. When a channel `/videos` page URL is passed, Bright Data returns `"Unable to find video ID in input.url"` (bad_input) — confirmed in this session's prior testing. The pipeline maps to the "collect by URL" dataset, which expects `youtube.com/watch?v={video_id}` URLs only. ([Bright Data CLI first-request docs](https://docs.brightdata.com/datasets/scrapers/youtube/send-first-request) — URL pattern table confirms `youtube.com/watch?v={video_id}` for the Videos dataset)

**Can `youtube_videos` accept a channel URL?** No — the `youtube_videos` pipeline type maps to the "collect by URL" dataset (`gd_lk56epmy2i5g7lzu0k`), whose URL pattern is `youtube.com/watch?v={video_id}`. Channel URLs are rejected. ([Bright Data CLI first-request docs](https://docs.brightdata.com/datasets/scrapers/youtube/send-first-request))

**Does Bright Data have a "channel recent videos" pipeline?** The Bright Data YouTube Scraper API supports a **discovery mode** where you pass a channel or playlist URL to collect all videos from it. From the Bright Data product FAQ: "You can also discover videos by passing a YouTube channel or playlist URL to collect all videos from it." However, this discovery mode appears to use a **different dataset ID** (not the `youtube_videos` pipeline's `gd_lk56epmy2i5g7lzu0k`), and the `bdata pipelines` CLI does not expose a separate `youtube_videos_by_channel` pipeline type. The CLI's `youtube_videos` type sends the input URL to the "collect by URL" dataset, which rejects non-video URLs. ([Bright Data YouTube Scraper product page](https://brightdata.com/products/web-scraper/youtube) — FAQ section: "Can I discover YouTube videos and channels without knowing specific URLs?")

**`youtube_profiles` pipeline:** The `youtube_profiles` pipeline type (`gd_lk538t2k2p1k3oos71`) accepts a channel URL (`youtube.com/@{handle}`) and returns: channel name, handle, subscriber count, total views, video count, creation date, description, verification status, profile/banner images. It does **NOT** return individual video metadata (view counts per video, upload dates). ([Bright Data CLI pipelines reference](https://github.com/brightdata/skills/blob/main/skills/brightdata-cli/references/pipelines.md), [Bright Data YouTube Channels collect-by-url](https://brightdata.com/products/web-scraper/youtube/channels))

**Combination approach (the viable path):**
1. Use **RSS feeds** (see §5) to get the list of recent video URLs + view counts for each channel — free, works from any IP.
2. Use **`bdata pipelines youtube_profiles`** to get subscriber counts for each channel — works from GHA, handles anti-bot internally (same as the Reddit agent).
3. Optionally use **`bdata pipelines youtube_videos`** for each video URL to get richer metadata (likes, comments, transcript) — costs per-video API credits.

This combination leverages the existing `bdata` infrastructure (the repo already has `bdata` configured for the Reddit agent) and avoids any datacenter IP issues.

| Criterion | Assessment |
|-----------|------------|
| **Works on GHA?** | Yes — `bdata pipelines` uses Bright Data's infrastructure (proxy rotation, anti-bot bypassing). Same mechanism as the working Reddit agent. |
| **Cost** | 5,000 free credits/month (~$7.50). Scraper API pricing: ~$0.75/1k records. For 23 channels × ~10 videos = ~230 records/day → ~$0.17/day or ~$5.10/month. Well within free tier. |
| **Fields** | Video: views, likes, num_comments, date_posted, title, youtuber, subscribers, channel_url, description, tags. Channel (via youtube_profiles): subscriber count, total views, video count. |
| **Code change** | Moderate. Replace yt-dlp subprocess calls with `bdata pipelines` subprocess calls (same async pattern as `reddit_source.py`). Need a two-step flow: RSS for video URLs → `bdata pipelines youtube_videos` for metadata, OR just RSS + `bdata pipelines youtube_profiles`. ~100-150 lines changed. |
| **Daily cron viable?** | Yes. ~23 channel calls + ~230 video calls per day, well within rate limits. |

**Caveats:** The `youtube_videos` pipeline cannot accept channel URLs directly — you need a separate mechanism to discover recent video URLs (RSS feeds or YouTube Data API). The Bright Data discovery-by-channel mode exists in the API but is not exposed as a distinct CLI pipeline type. The team should verify whether calling the REST API directly with the correct dataset ID for discovery-by-channel works, or fall back to the RSS + youtube_profiles combination.

---

### 2. YouTube Data API v3 (search.list + videos.list + channels.list) — VIABLE

**What it is:** Google's official API for YouTube data. Works from any IP (it's an API, not scraping — no datacenter IP blocking).

**Authentication:** Requires a Google Cloud project with the YouTube Data API v3 enabled and an API key (free, no OAuth needed for public data). ([YouTube Data API getting started](https://developers.google.com/youtube/v3/getting-started))

**Quota system:** Default daily quota is **10,000 units** per project, resetting at midnight Pacific Time. The API is free; there is no paid tier. Quota increases require a review request to Google. ([YouTube Data API quota calculator](https://developers.google.com/youtube/v3/determine_quota_cost))

**Quota costs per method:**
- `search.list`: **100 units** per call ([YouTube Data API quota calculator](https://developers.google.com/youtube/v3/determine_quota_cost))
- `videos.list`: **1 unit** per call
- `channels.list`: **1 unit** per call

**Daily quota calculation for 23 channels:**
1. **`channels.list` with `forHandle`** — convert 23 handles to channel IDs + get subscriber counts. Can batch up to 50 channel IDs per call, but `forHandle` takes one handle per call. 23 calls × 1 unit = **23 units**. (Alternatively, resolve handles once and cache.)
2. **`search.list` per channel** — `part=snippet`, `type=video`, `channelId=...`, `order=date`, `publishedAfter={14-days-ago}`, `maxResults=10`. One call per channel = 23 calls × 100 units = **2,300 units**.
3. **`videos.list`** — batch all returned video IDs (up to 50 per call) to get `statistics.viewCount`, `statistics.likeCount`, `statistics.commentCount`, `snippet.publishedAt`. ~1-2 calls × 1 unit = **1-2 units**.

**Total: ~2,324-2,325 units/day** — well within the 10,000-unit daily quota. Even with pagination (unlikely for 10 results per channel), it stays well under the limit.

**Handle-to-channel-ID conversion:** The `channels.list` endpoint supports the `forHandle` parameter, which accepts a YouTube handle (with or without `@`). Example: `channels.list?part=snippet,statistics&forHandle=@MattPocock&key=...` returns the channel ID, subscriber count, total view count, and video count. ([YouTube Data API channels.list docs](https://developers.google.com/youtube/v3/docs/channels/list))

**Fields provided:**
- `search.list` → video ID, title, channel ID, publish date, description, thumbnails
- `videos.list` → view count, like count, comment count, publish date, title, channel ID, duration
- `channels.list` → subscriber count, total view count, video count, channel description

All fields the current yt-dlp source extracts (view count, channel, subscribers, date) are available.

| Criterion | Assessment |
|-----------|------------|
| **Works on GHA?** | Yes — it's a REST API, not scraping. No IP blocking. |
| **Cost** | Free. 10,000 units/day default quota. ~2,325 units/day used = 23% of quota. No $ cost. |
| **Fields** | Views, likes, comment count, publish date, title, channel ID, subscriber count, total channel views, video count. Complete coverage. |
| **Code change** | Moderate. Replace yt-dlp subprocess calls with HTTP requests to `googleapis.com/youtube/v3/...` using `aiohttp` or `requests`. Need to add `GOOGLE_API_KEY` (or `YOUTUBE_API_KEY`) to GHA secrets. ~100-150 lines changed. The channel URL list (`CHANNELS` in source.py) stays the same — just parse handles instead of `/videos` URLs. |
| **Daily cron viable?** | Yes. 23% of daily quota used. Massive headroom for retries, additional channels, or more frequent runs. |

**Evidence:** The `search.list` endpoint with `channelId` + `publishedAfter` + `order=date` is the standard method for listing a channel's recent uploads. ([YouTube Data API search.list docs](https://developers.google.com/youtube/v3/docs/search/list))

---

### 3. yt-dlp with a Different Transport — NOT-VIABLE

**What it is:** Workarounds to make yt-dlp work from a datacenter IP by changing the network transport or client identity.

**Options investigated:**

**a) Cloudflare WARP:**
YouTube generally does not block Cloudflare's IP ranges. Running WARP as a SOCKS5/HTTP proxy (via `warproxy` or `wireproxy` — userspace WireGuard, no root needed) and pointing yt-dlp at it (`--proxy socks5://...`) is the most promising workaround. However, this requires installing and running WARP inside the GHA runner, which adds complexity and may break with GHA runner updates. ([yt-dlp community discussions](https://www.reddit.com/r/youtubedl/comments/1v1ifrs/ytdlp_blocked_on_aws_403sign_in_how_are_you/), [Cloudflare WARP workaround blog](https://blog.arfevrier.fr/leveraging-cloudflare-warp-to-bypass-youtubes-api-restrictions/))

**b) PO Token plugins:**
YouTube increasingly requires "Proof of Origin" (PO) Tokens for video requests. Without them, requests get 403 Forbidden or IP/account blocks. The `yt-dlp-get-pot` plugin framework and provider plugins (`bgutil-ytdlp-pot-provider`, `yt-dlp-getpot-wpc`) can auto-fetch tokens, but they require an external PO token server (`YT_DLP_POT_PROVIDER_URL` environment variable). This adds infrastructure and is fragile — tokens are now bound to specific video IDs. ([yt-dlp PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/Po-Token-Guide), [yt-dlp-get-pot on PyPI](https://pypi.org/project/yt-dlp-get-pot/))

**c) Client rotation / extractor-args:**
Using `--extractor-args "youtube:player_client=web,android_vr,tv_downgraded"` cycles through different player clients to bypass bot detection. This is fragile — breaks with YouTube updates, and may still be blocked from datacenter IPs. ([yt-dlp wiki](https://github.com/yt-dlp/yt-dlp/wiki/extractors))

**d) Visitor data:**
`--extractor-args` can pass visitor data without cookies, but this is "generally not recommended due to its instability." ([yt-dlp community discussions](https://forum.getassist.net/threads/how-to-solve-yt-dlp-sign-in-to-confirm-youre-not-a-bot-issue.5028/))

| Criterion | Assessment |
|-----------|------------|
| **Works on GHA?** | Uncertain/fragile. Cloudflare WARP might work but requires setup. PO tokens need external infra. Client rotation breaks with YT updates. |
| **Cost** | WARP is free. PO token server costs infra. All approaches require ongoing maintenance. |
| **Fields** | Same as current yt-dlp (views, channel, subscribers, date). |
| **Code change** | Minimal (just add proxy/extractor-args to existing yt-dlp calls). |
| **Daily cron viable?** | No — too fragile for unattended daily production. YouTube's anti-bot evolves continuously. yt-dlp may soon require a full JS runtime (Deno) to handle YouTube's JS challenges. ([OSNews: yt-dlp will soon require a full JS runtime](https://www.osnews.com/story/143423/yt-dlp-will-soon-require-a-full-js-runtime-to-overcome-youtubes-js-challenges/)) |

**Verdict: NOT-VIABLE** for a production daily cron. All workarounds are arms races against YouTube's anti-bot — they work today and break tomorrow. The user explicitly ruled out the residential proxy approach (`YOUTUBE_PROXY_URL`), and these alternatives are in the same fragility category.

---

### 4. Self-Hosted GHA Runner / Hetzner — NOT-VIABLE

**What it is:** Run the YouTube agent on a non-GHA host (a self-hosted GHA runner on a residential IP, or the existing Hetzner VPS).

**Self-hosted GHA runner requirements:** A machine running Linux (Ubuntu 20.04+), macOS 11+, or Windows 10+. Minimum 2 CPU cores, 4 GB RAM, 20 GB disk. Continuous outbound communication with GitHub Actions (`*.actions.githubusercontent.com`). The runner application is installed and registered with the GitHub repo/org. ([GitHub Actions self-hosted runner docs](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners), [Self-hosted runner reference](https://docs.github.com/en/actions/reference/runners/self-hosted-runners))

**Is the Hetzner VPS a datacenter IP?** Yes — Hetzner is a cloud hosting provider. Their IPs are datacenter IPs, just like GHA. YouTube's anti-bot blocks datacenter IPs regardless of provider. The Hetzner VPS would face the same yt-dlp 0-results problem as GHA. ([yt-dlp community: "yt-dlp blocked on AWS 403/sign in"](https://www.reddit.com/r/youtubedl/comments/1v1ifrs/ytdlp_blocked_on_aws_403sign_in_how_are_you/))

**Residential-IP runner:** A self-hosted GHA runner on a residential IP (e.g., the dev's Mac, a home server, or a residential proxy VPS) would work — YouTube doesn't block residential IPs. But this adds significant infra burden: the machine must be always-on, maintained, and connected.

| Criterion | Assessment |
|-----------|------------|
| **Works on GHA?** | Hetzner: No (datacenter IP, same blocking). Residential runner: Yes. |
| **Cost** | Hetzner VPS already exists (no new $). Residential runner: hardware + electricity + maintenance. |
| **Fields** | Same as current yt-dlp. |
| **Code change** | None — just move the job to a different runner. |
| **Daily cron viable?** | Hetzner: No. Residential runner: Yes, but requires always-on infra. |

**Verdict: NOT-VIABLE.** The Hetzner VPS is a datacenter IP and would be blocked just like GHA. A residential runner works but adds operational burden that the team likely doesn't want to maintain for a single daily cron job.

---

### 5. RSS Feeds — VIABLE-WITH-CAVEATS

**What it is:** YouTube channels expose RSS feeds at `https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}` that return the 15 most recent uploads. These are public Atom XML feeds that work from any IP (no anti-bot, no datacenter blocking).

**Fields included in the RSS feed:**
- Video title, link, video ID
- Thumbnail
- Description
- Publish date (`<published>`)
- Channel name
- **View count** via `<media:statistics views="...">` within `<media:community>` — confirmed by multiple sources. ([WP RSS Aggregator: YouTube RSS feed](https://www.wprssaggregator.com/youtube-rss-feed/), [Alibaba tech tips: Google video RSS feeds](https://lifetips.alibaba.com/tech-efficiency/google-video-rss-feeds))

Example XML structure for view counts:
```xml
<media:community>
  <media:starRating count="974" average="5.00" min="1" max="5"/>
  <media:statistics views="21210"/>
</media:community>
```

**Fields NOT included:**
- Subscriber count (not available in RSS — need `bdata pipelines youtube_profiles` or YouTube Data API `channels.list`)
- Likes, comment count (not in RSS)

**Getting channel_id from a handle:**
The RSS feed requires a `channel_id` (e.g., `UCxxxxxxx`), not a handle (e.g., `@MattPocock`). Options:
1. **YouTube Data API** `channels.list?forHandle=@MattPocock&part=id` — 1 unit per call. ([YouTube Data API channels.list docs](https://developers.google.com/youtube/v3/docs/channels/list))
2. **One-time manual lookup** — channel IDs are stable and don't change. Resolve all 23 handles once and hardcode the channel IDs.
3. **YouTube oEmbed** — `https://www.youtube.com/oembed?url=https://www.youtube.com/@MattPocock&format=json` returns the channel name but not the channel ID directly.

**RSS feed reliability:** Some sources indicate YouTube "deprecated" official RSS support in 2023, but the feeds still work as of 2025/2026. The feed returns 15 most recent videos, which is more than enough for a 14-day window with 10 videos per channel. ([Reddit: YouTube RSS feeds](https://www.reddit.com/r/rss/comments/1fbrqef/is_it_possible_to_get_an_rss_feed_for_youtube/))

| Criterion | Assessment |
|-----------|------------|
| **Works on GHA?** | Yes — it's a public XML feed, no anti-bot, no datacenter blocking. |
| **Cost** | Free. No API key, no quota, no $ cost. |
| **Fields** | Title, video URL, video ID, publish date, view count, channel name, thumbnail. Missing: subscriber count, likes, comments. |
| **Code change** | Moderate. Replace yt-dlp subprocess with `feedparser` or `aiohttp` + XML parsing. Need to resolve 23 handles to channel IDs (one-time or via API). ~80-120 lines changed. |
| **Daily cron viable?** | Yes — 23 HTTP GET requests per day, trivial load. |

**Combination with bdata:**
RSS feeds give view counts and video URLs for free. To get subscriber counts (needed for channel-relative velocity), combine with `bdata pipelines youtube_profiles "https://youtube.com/@handle"` — one call per channel, handles anti-bot internally, returns subscriber count. This gives a complete data set using only free resources + existing bdata infrastructure.

---

### 6. Other Approaches

**a) RSS + `bdata pipelines youtube_videos` (hybrid):**
Use RSS to get recent video URLs + view counts (free), then call `bdata pipelines youtube_videos` for each video URL to get full metadata (likes, comments, subscribers, transcript). This gives the richest data but costs per-video API credits. For 23 channels × ~5 new videos/day = ~115 video calls/day → ~$0.09/day → ~$2.70/month. Well within the 5,000 free credits/month tier. **VIABLE** — best data quality within existing bdata infrastructure.

**b) YouTube oEmbed API:**
`https://www.youtube.com/oembed?url=...&format=json` returns minimal metadata (title, author, thumbnail). No view counts. Not useful as a primary source.

**c) Third-party metadata APIs (e.g., Apify, SocialCrawl):**
Various third-party services offer YouTube scraping APIs, but they add external dependencies and cost. Not recommended when the YouTube Data API v3 is free and official.

**d) Cached/snapshot video metadata APIs:**
Services like SocialCrawl.dev offer cached YouTube data, but these are unofficial and may not have real-time view counts. Not recommended.

---

## Viability Summary Table

| Alternative | Verdict | GHA? | Cost | Fields | Code Change | Daily Cron? |
|---|---|---|---|---|---|---|
| **YouTube Data API v3** | **VIABLE** | Yes (API) | Free (10k units/day, ~2,325 used) | Views, likes, comments, subscribers, dates, title | Moderate (~100-150 lines) | Yes (23% quota) |
| **RSS + bdata youtube_profiles** | **VIABLE-WITH-CAVEATS** | Yes (RSS + bdata) | Free (RSS) + bdata credits | Views, dates, title (RSS); subscribers (bdata) | Moderate (~120-180 lines) | Yes |
| **RSS + bdata youtube_videos** | **VIABLE-WITH-CAVEATS** | Yes (RSS + bdata) | Free (RSS) + ~$2.70/mo bdata | Full video metadata + subscribers | Moderate (~150-200 lines) | Yes |
| **bdata youtube_videos alone** | **VIABLE-WITH-CAVEATS** | Yes (bdata) | ~$5/mo | Full video metadata | Needs channel→video URL bridge | Yes |
| **RSS feeds alone** | **VIABLE-WITH-CAVEATS** | Yes (public feed) | Free | Views, dates, title (no subscribers) | Moderate (~80-120 lines) | Yes (but missing subscriber data) |
| **yt-dlp + Cloudflare WARP** | **NOT-VIABLE** | Fragile | Free + infra | Same as yt-dlp | Minimal | No (fragile) |
| **yt-dlp + PO tokens** | **NOT-VIABLE** | Fragile | External server | Same as yt-dlp | Minimal | No (fragile) |
| **Self-hosted runner (Hetzner)** | **NOT-VIABLE** | No (datacenter IP) | Existing VPS | Same as yt-dlp | None | No |
| **Self-hosted runner (residential)** | **VIABLE-WITH-CAVEATS** | Yes (residential IP) | Hardware + maintenance | Same as yt-dlp | None | Yes but infra burden |

---

## Ranked Recommendation

### #1: YouTube Data API v3 (RECOMMENDED)

**Why:** The cleanest, most reliable, and most cost-effective solution:
- **Free** — no $ cost, 10,000 units/day quota, using only ~2,325/day (23%)
- **All fields** — views, likes, comments, subscribers, dates, title (complete coverage of current yt-dlp output)
- **Works from any IP** — it's a REST API, no scraping, no anti-bot issues
- **Official and stable** — Google's API, not a scraping workaround
- **Low code change** — replace yt-dlp subprocess with HTTP calls to `googleapis.com/youtube/v3/...`
- **Scales** — massive quota headroom for more channels or more frequent runs

**Implementation sketch:**
1. Create a Google Cloud project, enable YouTube Data API v3, generate an API key
2. Add `YOUTUBE_API_KEY` (or `GOOGLE_API_KEY`) to GHA secrets
3. For each of 23 channels: `channels.list?forHandle=@handle&part=snippet,statistics` → channel ID + subscriber count
4. For each channel: `search.list?part=snippet&type=video&channelId=...&order=date&publishedAfter={14d-ago}&maxResults=10` → recent video IDs
5. Batch all video IDs: `videos.list?part=snippet,statistics&id=...` → view counts, likes, comments, dates
6. Map to existing data structure (view count, channel, subscribers, date)

### #2: RSS feeds + bdata youtube_profiles (FALLBACK)

**Why:** Stays within the existing Bright Data ecosystem, uses only free resources:
- RSS feeds give view counts + dates + video URLs for free (public feeds, any IP)
- `bdata pipelines youtube_profiles` gives subscriber counts (handles anti-bot internally)
- No new external dependency (bdata already configured for Reddit agent)
- Slightly more code than the API approach (XML parsing + two data sources to merge)

**When to choose this:** If the team wants to avoid adding a Google Cloud project / API key, or wants to stay within the existing bdata infrastructure.

### #3: RSS feeds + bdata youtube_videos (RICH DATA)

**Why:** Richest data within existing bdata infrastructure:
- RSS for video URLs (free), `bdata pipelines youtube_videos` for full metadata per video
- Gives likes, comments, transcript, tags — more than the current yt-dlp source
- Costs ~$2.70/month (well within free tier)
- More API calls but still viable for 23 channels

**When to choose this:** If the team wants maximum data quality and is OK with per-video API calls.

---

## Gaps

1. **RSS view count reliability:** Some sources indicate YouTube RSS feeds include `<media:statistics views="...">`, but others suggest this may have been deprecated or inconsistent. The team should verify by fetching a sample RSS feed from a GHA runner and checking for view count fields.
2. **Bright Data discovery-by-channel dataset ID:** The Bright Data API supports discovery-by-channel mode, but the specific dataset ID for this mode (distinct from `gd_lk56epmy2i5g7lzu0k` for collect-by-URL) was not found in the public docs. The team should check `bdata pipelines list` output or the Bright Data dashboard for a "youtube_videos_by_channel" or "youtube_channel_videos" dataset type.
3. **YouTube Data API search.list quota change:** The official quota page (last updated 2026-06-01) mentions a possible change where search.list gets a separate 100-call/day bucket at 1 unit each (instead of 100 units per call). This would make the API approach even cheaper. The team should verify the current quota cost by checking the Google Cloud Console quota page.
4. **RSS feed channel_id resolution:** The RSS feed requires `channel_id` (not handle). The team needs a one-time resolution of 23 handles to channel IDs, or a runtime lookup via the YouTube Data API or oEmbed.

## Sources

### Kept
- [YouTube Data API quota calculator](https://developers.google.com/youtube/v3/determine_quota_cost) — official quota costs for all API methods
- [YouTube Data API search.list docs](https://developers.google.com/youtube/v3/docs/search/list) — search.list parameters and usage
- [YouTube Data API channels.list docs](https://developers.google.com/youtube/v3/docs/channels/list) — forHandle parameter, statistics fields
- [Bright Data CLI pipelines reference](https://github.com/brightdata/skills/blob/main/skills/brightdata-cli/references/pipelines.md) — full list of pipeline types, parameters, how pipelines work
- [Bright Data CLI first-request docs](https://docs.brightdata.com/datasets/scrapers/youtube/send-first-request) — dataset IDs and URL patterns for videos/channels/comments
- [Bright Data YouTube Videos collect-by-url](https://docs.brightdata.com/api-reference/scrapers/social-media-apis/youtube-videos-collect-by-url) — video response schema with all fields
- [Bright Data YouTube Scraper product page](https://brightdata.com/products/web-scraper/youtube) — FAQ on discovery modes, pricing, free tier
- [Bright Data async requests docs](https://docs.brightdata.com/datasets/scrapers/youtube/async-requests) — async trigger endpoint, limits
- [Bright Data CLI npm page](https://www.npmjs.com/package/@brightdata/cli) — CLI overview, command table
- [GitHub Actions self-hosted runner docs](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners) — runner requirements
- [yt-dlp PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/Po-Token-Guide) — PO token requirements and provider plugins
- [yt-dlp-get-pot on PyPI](https://pypi.org/project/yt-dlp-get-pot/) — PO token provider framework
- [WP RSS Aggregator: YouTube RSS feed](https://www.wprssaggregator.com/youtube-rss-feed/) — RSS feed fields and structure
- [Cloudflare WARP workaround blog](https://blog.arfevrier.fr/leveraging-cloudflare-warp-to-bypass-youtubes-api-restrictions/) — WARP as proxy for yt-dlp
- [yt-dlp community: blocked on AWS](https://www.reddit.com/r/youtubedl/comments/1v1ifrs/ytdlp_blocked_on_aws_403sign_in_how_are_you/) — datacenter IP blocking confirmed
- [OSNews: yt-dlp will soon require a full JS runtime](https://www.osnews.com/story/143423/yt-dlp-will-soon-require-a-full-js-runtime-to-overcome-youtubes-js-challenges/) — future fragility of yt-dlp

### Dropped
- Various SEO/blog posts about yt-dlp proxies (roundproxies.com, medium.com) — not primary sources, redundant with the yt-dlp wiki
- Apify/BrowseAI/third-party scraper pages — not relevant to the existing bdata infrastructure
- Snowflake marketplace listing — enterprise data delivery, not relevant to a CLI-based daily cron
