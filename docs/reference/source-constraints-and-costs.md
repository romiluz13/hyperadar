# Source Constraints and Run Budget

> Current code paths and known operational gates. Prices and quotas can change;
> verify them before presenting a cost forecast.

## Current source adapters

| Agent | Current source path | Per-run boundary |
| --- | --- | --- |
| `@github-radar` | GitHub REST search/details | Requires GitHub token for dependable rate limits |
| `@reddit-pulse` | Two Bright Data `bdata search` queries | Requires CLI plus `BRIGHTDATA_API_KEY` |
| `@youtube-trends` | Two `yt-dlp` `ytsearch5` queries | Installs the pinned CLI in GitHub Actions |
| `@hidden-gems` | Top 20 Hacker News stories plus one GitHub repository search | HN is unauthenticated; GitHub token is preferred |
| `@weekly-digest` | Up to 15 distinct projects from synchronized source-agent posts in the last seven days | No external discovery source |

The adapters store the metric they actually observed. GitHub's stars/week value
is a lifetime average since repository creation, not recent velocity; sustained
growth requires six observations across five weeks. HN points remain HN points.
YouTube keeps total views plus the position within its specific YouTube search
query. Reddit keeps Google result rank and labels its visibility score as a
proxy. None is converted into fake stars, a fabricated Google rank, or
unobserved Reddit votes/comments.

The weekly editor may connect themes and name projects, but it cannot introduce
engagement counts, rates, velocity, or sustained-growth claims. Readers are sent
to project dossiers for source-labeled measurements. Its rank is the average of
the included source-project momentum scores; editorial digest projects are
excluded so the wrapper cannot rank itself.

## Current execution cadence

The verified production control path is on-demand through the Port Workflow and
GitHub Actions. Repository scripts may support manual batch runs, but a scheduled
daily cron is not a current production claim.

GitHub's workflow serializes concurrent runs per selected agent and gives each
job a 30-minute timeout. Every package runs from its committed `uv.lock` with
`--frozen`.

## Known gate

`BRIGHTDATA_API_KEY` is not configured in the challenge GitHub repository, so the
Reddit workflow must be described as blocked. The existing canonical Reddit post
was repaired from directly verified source evidence; that does not prove the
automated Reddit runner is currently available.

## Cost discipline

- Bound discovery result counts before calling the model.
- Use MongoDB reads only for the weekly digest input.
- Keep agent runs operator-triggered until source quality and cost are measured.
- Treat LLM, Bright Data, Atlas, Port, GitHub Actions, and Vercel prices as live
  external facts; refresh them before recording exact numbers.
- A source failure must fail the run instead of silently publishing empty or
  synthetic evidence.
