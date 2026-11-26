# ADR 0007: GitHub trending is @github-radar's primary source

**Date:** 2026-11-26
**Status:** Accepted. Grilled plan, `.scratch/hyperadar-refactor/spec.md`. Supersedes ADR 0003 for @github-radar only; hidden-gems keeps ADR 0003.

## Context

Rom: "The most important findings are from GitHub trending … Must make sure that it really works and fetches."

@github-radar read the trending pages only for slugs. A trending repo had to collect 7 or more days of snapshots and pass the momentum gate before it could post, so the repos GitHub itself called hot arrived late or never. Event datasets cannot replace trending. Measured on 2026-11-26, ClickHouse `github_events` and GH Archive captured only 0.6–1.5% of real stars across 10 trending repos. GitHub has also restricted per-stargazer listing since July 2026.

## Decision

- **Discovery:** github.com/trending pages: daily and weekly all-languages, daily python and daily typescript. At most 6 page fetches a day, with a generic descriptive User-Agent and one retry.
- **Velocity:** `GET /repos/{o}/{r}/stargazers/history`, the full-fidelity history GitHub documents, gives stars this week and stars today for every candidate (`CONTEXT.md`).
- **Selection:** plain code, with no LLM in selection. Candidates are filtered to AI/dev-tools scope, checked for star farming, and ordered by the hype score.
- **Failure handling:** the parser raises when the markup changes. When trending fails, discovery falls back to REST search plus star history.

## Consequences

- A repo can reach the digest the day it trends.
- The trending HTML is unofficial and may change without notice; the loud-failure rule and fixture tests catch that. GitHub's terms on scraping are Rom's call, and the REST fallback covers a "no".
- Fake-star detection by actor diversity is no longer possible. A fork and burst check carries that job.
