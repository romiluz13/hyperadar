# Source Constraints & Cost Model

> Verified 2026-07-09. These constraints are load-bearing for the agent-creator design.

## GitHub trending — NO official API

- No `/trending` endpoint. Anonymous scraping returns `404`/`429` after 1-3 requests. Authenticated scraping risks account flagging.
- **Solution:** use aggregators (OSSInsight, Trendshift) for discovery; GitHub REST API (5k req/h with token) for repo details (stars, topics, README).
- Repo details endpoint: `GET /repos/{owner}/{repo}` — cheap, 1 req each.

## Reddit API — bypassed via Bright Data

- Official Reddit API: free tier = non-commercial only (100 QPM, "Responsible Builder" approval); commercial = ~$0.24/1k calls, ~$12k/yr floor. `.json` endpoints now `403`.
- **Decision for HypeRadar: use Bright Data Reddit scraper (`bdata`)** — ~$1.50/1000 records, ~$0.30/day at our once-daily ~200-record volume. No commercial gate, no approval wait. Rom has `bdata` CLI + the `data-feeds`/`scrape` skills installed.

## YouTube Data API — quota, no paid tier

- Free: 10k units/day. `search.list` = 100 units (expensive). `videos.list` = 1 unit (cheap).
- **No paid tier** — can't buy more quota; extension requires manual compliance audit (2-8 weeks).
- **Solution:** `@youtube-trends` tracks a **seed list of known AI/dev channels** by ID. Use `videos.list` (1 unit) to fetch their recent videos + stats. 10k units/day = ~10k video lookups — plenty for once-daily.
- Avoid `search.list` entirely. Build/maintain the channel seed list manually (or via a one-time setup).

## Crons (once daily — cost-conscious)

| Agent | Cron | Primary source | Calls/day | LLM calls |
| --- | --- | --- | --- | --- |
| `@github-radar` | Daily 06:00 | OSSInsight/Trendshift + GitHub API | ~50 | ~20 |
| `@reddit-pulse` | Daily 07:00 | Bright Data Reddit scraper (`bdata`) | ~200 | ~15 |
| `@youtube-trends` | Daily 08:00 | YouTube `videos.list` on seed channels | ~100 | ~10 |
| `@hidden-gems` | Daily 09:00 | HN API + GitHub low-star repos | ~30 | ~10 |
| `@weekly-digest` | Mon 09:00 | MongoDB reads only | 0 | ~1 |

## Daily cost drivers

- LLM calls: ~55 blurbs/verdicts/day (agent voices) — main variable cost
- MongoDB: auto-embedding (~50/day), `$rerank` (~25/day), Atlas tier
- Port: Ocean integration runs (5/day + 1 weekly)
- Vercel: compute (frontend reads + Python Sandbox agent runs; free tier covers once-daily cadence)
- Reddit API: free OR $12k/yr (the one big risk)

**Once-daily crons keep variable costs low.** Bright Data Reddit (~$0.30/day) is the only material external cost. LLM calls go through **Grove** (MongoDB gateway, no external cost). MongoDB Atlas (staff access) + Port (partnership) + Vercel (free tier covers once-daily agents) are effectively free for this project.

## Framing the cadence

Once-daily isn't a limitation — it's the product's rhythm. "HypeRadar drops daily — the radar refreshes every morning." Hype isn't minute-by-minute; a daily drop feels like an event (morning radar) and fits a "this week in AI dev" mental model. Lean into it in the UX.
