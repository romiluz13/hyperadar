# ADR 0003: Breakout Prediction Momentum Score for Hidden Gems

**Date:** 2026-07-21
**Status:** Accepted

## Context

The @hidden-gems agent surfaced repos with 10–200 stars using a dumb GitHub
Search filter (`stars:10..200 topic:ai sort:updated`). There was no prediction
logic — a 19-star repo appeared because it exists, not because it's
accelerating. The agent didn't track star velocity, acceleration, or any signal
that distinguishes "about to break out" from "just exists."

Research into existing tools (OSS Pulse, Trendshift, RepoInsider, CMU StarScout)
identified that real breakout prediction requires:

1. Time-series tracking of star counts over multiple days
2. A composite Momentum Score (velocity + acceleration + relative growth)
3. Fake-star filtering (CMU research: 6M fake stars, 16% of 50+ star repos)
4. A publishing gate that requires sustained acceleration

## Decision

Implement a breakout prediction pipeline for @hidden-gems:

1. **Momentum Score (0–100):** Velocity 35% + Acceleration 25% + Relative
   Growth 20% + Engagement Depth 10% + Consistency 10% + Viral Bonus (+10).
   Formula borrowed from OSS Pulse with modifications for HypeRadar's MongoDB
   time-series signals.

2. **Fake-star filter:** Reject repos with fork/star ratio < 0.02 (CMU
   StarScout threshold). Repos with stars but zero forks are suspicious. Repos
   with zero stars pass through (can't evaluate).

3. **Publishing gate:** A repo is published only when ALL conditions are met:
   - Momentum Score ≥ 55
   - Velocity > 0 (currently growing)
   - Acceleration > 0 (growth is accelerating)
   - Fork/star ≥ 0.02 (passes fake-star filter)
   - Not published in the last 14 days (dedup)

4. **Daily snapshot tracking:** The `signals` time-series collection stores
   daily star/fork snapshots. The tracker runs as part of the daily cron and
   is idempotent (re-running same day doesn't duplicate).

5. **All-agents improvements:**
   - @github-radar: fake-star filter added (same fork/star threshold)
   - @reddit-pulse: sort changed from "hot" to "rising"
   - @youtube-trends: view velocity tracking added

## Alternatives considered

- **Atlas auto-embeddings (storedSource):** Rejected — only auto-embeds
  documents, not queries. The web app still needs Voyage REST API for
  query-time embeddings.

- **Simple threshold (stars > N):** Rejected — doesn't distinguish "exists"
  from "accelerating." A 19-star repo with flat growth is not a hidden gem.

- **Per-stargazer API calls for deep fake-star detection:** Deferred to a
  future phase — requires many API calls per repo. The fork/star ratio is
  free (available from the GitHub Search API response) and catches the most
  obvious fakes.

- **GH Archive ingestion:** Deferred — too heavy for the current scale.
  GitHub Search API + MongoDB time-series is sufficient for tracking hundreds
  of candidates.

## Consequences

- **Hidden-gems will publish fewer posts** — only repos with ≥7 days of
  acceleration history and a Momentum Score ≥55 will appear. This is the
  intended behavior: quality over quantity.

- **First 7 days of tracking produce no hidden-gems posts** — the algorithm
  needs history to compute velocity and acceleration. New repos must be
  tracked for at least a week before they can be published.

- **The fake-star filter may reject some legitimate repos** — repos with
  genuinely low fork counts but real momentum will be filtered. This is an
  acceptable tradeoff: false negatives are better than false positives
  (bot-inflated repos appearing as "hidden gems").

- **Reddit "rising" may return fewer posts than "hot"** — rising posts have
  less engagement, but they catch trends 4–12 hours earlier.
