# ADR 0004: YouTube Data API v3 replaces yt-dlp for the @youtube-trends source

**Date:** 2026-07-29
**Status:** Accepted

## Context

The @youtube-trends agent discovered AI-dev videos by shelling out to
`yt-dlp` to scan each channel's `/videos` page for recent uploads. This
worked from residential IPs but returned **zero results from GitHub Actions
datacenter IPs** — YouTube's anti-bot blocks datacenter IP ranges. The daily
`daily-radar-refresh` workflow ran the YouTube agent every day, but the
`yt-dlp` fetch silently returned `[]`, so no YouTube content reached the
digest for days at a time.

A research investigation (`docs/research/2026-07-28-youtube-source-gha-alternatives.md`)
evaluated nine alternatives: YouTube Data API v3, Bright Data `youtube_videos`
pipeline, RSS + Bright Data, yt-dlp transport workarounds (WARP, PO tokens),
self-hosted runners, and residential proxies. The research ranked YouTube Data
API v3 as the recommended replacement.

## Decision

Replace the `yt-dlp` subprocess source with **YouTube Data API v3** (`httpx`
REST calls):

1. **`channels.list` (`forHandle`)** — resolve each curated channel's ID +
   subscriber count.
2. **`search.list` (`channelId`, `order=date`, `publishedAfter=14d`)** —
   discover recent uploads per channel.
3. **Batched `videos.list` (`part=snippet,statistics`, 50 IDs/call)** — fetch
   view/like counts + publish dates.
4. **`YOUTUBE_API_KEY`** GHA secret (free tier, 10k units/day quota; ~2.3k
   used for 23 channels).
5. **Error handling:** 401/403 auth errors raise a global `RuntimeError` (a
   bad/expired key is not a per-channel soft-fail); non-auth errors log the
   HTTP status only (not the exception, which would leak the key via
   httpx's `raise_for_status()` embedding `?key=...` in the error message);
   `raise ... from None` suppresses the chained cause so a formatted traceback
   can't leak the key either.

The candidate dict shape (url, title, kind, channel, viewCount, uploadDate,
channel_url, channel_subscribers, like_count, description, topics) is
unchanged — the gate + velocity layer is untouched.

## Alternatives considered

- **Residential proxy for yt-dlp:** Rejected — requires provisioning a Bright
  Data residential proxy zone + ongoing proxy costs; yt-dlp transport
  workarounds (WARP, PO tokens) are too fragile for an unattended daily cron.
- **Bright Data `youtube_videos` pipeline:** Rejected — requires a
  video-URL discovery bridge (the pipeline rejects channel `/videos` URLs);
  adds a second data-source dependency.
- **RSS + Bright Data `youtube_profiles`:** Fallback — RSS can provide recent
  video URLs from any IP, but view-count reliability and channel-ID
  resolution remain gaps. Considered as a fallback if the API quota proves
  insufficient.
- **Self-hosted runner (non-datacenter):** Rejected — adds infrastructure
  burden; the API works from GHA datacenter IPs directly.

See `docs/research/2026-07-28-youtube-source-gha-alternatives.md` for the full
analysis.

## Consequences

- **YouTube content returns to the daily digest** — the API works from GHA
  datacenter IPs, restoring YouTube discovery after the yt-dlp datacenter
  block.

- **New dependency: `YOUTUBE_API_KEY` GHA secret** — both
  `daily-radar-refresh.yml` and `run-hyperadar-agent.yml` validate the key
  per matrix leg and pass it to the agent env. The key is a Google API key
  (not a bearer token); it must not be logged (httpx embeds it in error
  messages, so the source logs HTTP status only, never the exception).

- **Quota budget: ~2,325 of 10,000 daily units** — 23 channels.list (23u) +
  23 search.list (2,300u) + ~4 batched videos.list (4u). Well under the free
  tier; room to add channels without exceeding quota.

- **`commentCount` is fetched but not surfaced** — the API returns it in the
  `statistics` part, but the candidate dict omits it (matching the old yt-dlp
  source). Available if a downstream consumer ever needs it.

- **The yt-dlp install step is removed from both workflows** — no more
  `uv tool install yt-dlp==2026.07.04`.
