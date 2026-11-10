# ADR 0006: Reddit is read through Monid's free TinyFish fetch

**Date:** 2026-11-10
**Status:** Accepted. Replaces the Bright Data `bdata pipelines reddit_posts`
source in `integrations/reddit_pulse/reddit_source.py`.

## Context

`@reddit-pulse` read 16 subreddit `/rising/` feeds through the Bright Data CLI.
The Bright Data token expired around 2026-10-07. Every fetch failed, the run
reported a quiet day, and Reddit disappeared from the feed for five weeks
(`docs/research/2026-11-09-hype-radar-algorithm-audit.md`, finding 1). Bright Data
is also paid per record, and the operator requires the system to cost $0.

Monid (`https://api.monid.ai`) exposes TinyFish's `/free/fetch` endpoint at $0 a
call: 1-10 URLs per call, free up to 1,000 successfully fetched URLs a day and
150 URLs a minute, `402 INSUFFICIENT_CREDITS` beyond that
(docs.tinyfish.ai/fetch-api/reference). Probes on 2026-11-10 showed:

- Reddit's `.json` listings, `api.reddit.com` and `old.reddit.com` return 403 or
  `login_required` through TinyFish.
- `https://www.reddit.com/svc/shreddit/community-more-posts/rising/?name=<sub>`
  returns the feed markup. Each `<shreddit-post>` carries `score`,
  `comment-count`, `created-timestamp`, `id`, `permalink`, `post-title`,
  `subreddit-name` and `content-href`.
- One run over the 16 subreddits read 161 posts in 55 s at $0, with no failures.

Monid's paid Reddit endpoint (`anyapi /reddit.subreddit_posts`, $0.00038 a call)
was rejected because it costs money.

## Decision

`reddit_source.py` calls `POST /v1/run` with provider `tinyfish`, endpoint
`/free/fetch`, `ttl: 0`, `format: html` and `include_selectors: ["shreddit-post"]`,
in two calls of at most 10 feed URLs. It polls `GET /v1/runs/:runId` when Monid
answers asynchronously. It treats any status other than `COMPLETED`, a provider
`httpStatus` of 400 or more, or a page with no `<shreddit-post>` as a failed
subreddit. When every subreddit fails, the run raises.

The parser keeps the start tags of `shreddit-post` elements, dedupes them by
`id`, and builds the canonical URL `https://www.reddit.com/r/<sub>/comments/<id>`
so cooldown and snapshot history keep matching earlier posts. It also reads each
post's text body (`<div property="schema:articleBody" about="t3_…">`) into the
description, as the Bright Data feed did, so a repo named only in a post body
still corroborates in the snapshot corpus. A page where no post carries
`post-title`, `permalink` and a numeric `score` counts as a failed subreddit;
a page whose readable posts all sit under the 10-upvote noise floor is a quiet
subreddit. Scoring, heat, snapshots and the publish gate are unchanged.

`MONID_API_KEY` replaces `BRIGHTDATA_API_KEY` in the workflows, the doctor and
the Port catalog setup. The workflows no longer install the Bright Data CLI.

## Consequences

- Reddit costs $0 per run. Sixteen URLs a day use 1.6% of the free allowance.
- The source depends on Reddit's shreddit markup. A removed element or renamed
  attribute shows up as "no posts in the feed markup" on every subreddit, so the
  run fails loudly instead of going quiet.
- A Monid outage, an exhausted free tier (402) or a Reddit block fails the run
  the same way, and the daily refresh alert fires.
