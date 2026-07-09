# Source Constraints & Cost Model

> Verified 2026-07-09. These constraints are load-bearing for the agent-creator design.

## GitHub trending — NO official API

- No `/trending` endpoint. Anonymous scraping returns `404`/`429` after 1-3 requests. Authenticated scraping risks account flagging.
- **Solution:** use aggregators (OSSInsight, Trendshift) for discovery; GitHub REST API (5k req/h with token) for repo details (stars, topics, README).
- Repo details endpoint: `GET /repos/{owner}/{repo}` — cheap, 1 req each.

## Reddit API — non-commercial gate

- Free tier: **non-commercial only**, 100 QPM, requires "Responsible Builder" manual approval.
- Commercial: ~$0.24/1k calls, **~$12k/yr minimum floor**.
- `.json` endpoints (unauthenticated) largely return `403` now.
- **Decision needed:** is HypeRadar (partnership showcase) commercial? If yes → budget commercial OR reframe as research/personal for free-tier approval. Affects `@reddit-pulse` viability.

## YouTube Data API — quota, no paid tier

- Free: 10k units/day. `search.list` = 100 units (expensive). `videos.list` = 1 unit (cheap).
- **No paid tier** — can't buy more quota; extension requires manual compliance audit (2-8 weeks).
- **Solution:** `@youtube-trends` tracks a **seed list of known AI/dev channels** by ID. Use `videos.list` (1 unit) to fetch their recent videos + stats. 10k units/day = ~10k video lookups — plenty for once-daily.
- Avoid `search.list` entirely. Build/maintain the channel seed list manually (or via a one-time setup).

## Crons (once daily — cost-conscious)

| Agent | Cron | Primary source | Calls/day | LLM calls |
| --- | --- | --- | --- | --- |
| `@github-radar` | Daily 06:00 | OSSInsight/Trendshift + GitHub API | ~50 | ~20 |
| `@reddit-pulse` | Daily 07:00 | Reddit API (free if approved) | ~200 | ~15 |
| `@youtube-trends` | Daily 08:00 | YouTube `videos.list` on seed channels | ~100 | ~10 |
| `@hidden-gems` | Daily 09:00 | HN API + GitHub low-star repos | ~30 | ~10 |
| `@weekly-digest` | Mon 09:00 | MongoDB reads only | 0 | ~1 |

## Daily cost drivers

- LLM calls: ~55 blurbs/verdicts/day (agent voices) — main variable cost
- MongoDB: auto-embedding (~50/day), `$rerank` (~25/day), Atlas tier
- Port: Ocean integration runs (5/day + 1 weekly)
- Cloudflare: Workers requests (frontend reads)
- Reddit API: free OR $12k/yr (the one big risk)

**Once-daily crons keep variable costs to cents/day** on free/low tiers. Reddit commercial tier is the only material cost risk.

## Framing the cadence

Once-daily isn't a limitation — it's the product's rhythm. "HypeRadar drops daily — the radar refreshes every morning." Hype isn't minute-by-minute; a daily drop feels like an event (morning radar) and fits a "this week in AI dev" mental model. Lean into it in the UX.
