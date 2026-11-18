# ADR 0006: Reddit is read through Monid's free TinyFish fetch

**Date:** 2026-11-10
**Status:** Accepted. Replaces the Bright Data `bdata pipelines reddit_posts`
source in `integrations/reddit_pulse/reddit_source.py`. Amended 2026-11-18 (see
the last section): 19 subreddits, retries; the 16 below is the original count.

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

## Amendment 2026-11-18: three launch subreddits, a per-subreddit floor, retries

Nine scheduled runs after this ADR (2026-11-10 to 11-18) showed three things
(`.ddd/notes/github-share-and-reddit-sources.md`):

- Reddit answered `target_http_error 403` for 7 to 11 of the 16 feeds on every run
  from 11-12 on, a different set each day (LocalLLaMA failed some days and not
  others). The fetch now retries the URLs that came back as per-URL errors, two
  extra passes 5 s apart. The TinyFish free allowance is "counted per
  successfully fetched URL" (docs.tinyfish.ai/fetch-api/reference, read
  2026-11-18), so failures cost nothing; a call Monid refuses as a whole (402,
  429, outage) is not retried. The client timeout is 150 s, the docs'
  recommended minimum.
- The repos that launched in the month sat in r/OpenSourceAI, r/mcp and r/ollama
  with 3 to 72 upvotes. They join `SUBREDDITS` (19 feeds, 1.9% of the free
  allowance), and their parser floor is 1 upvote instead of 10 so a small launch
  post lands in the snapshot corpus that `@github-radar` matches repos against.
  The publish gate (`heat.py`: 20 upvotes, 1 comment, heat 40) is unchanged, and
  posts under 10 upvotes stay out of the heat baseline, so a 22-upvote post in
  r/mcp is not scored as a breakout against 2-upvote neighbours. These posts feed
  the corpus only, never the feed.
- Consequence: a run makes up to 19 + 2 x 19 URL attempts in the worst case, 57
  against a limit of 150 a minute, and only the successes use the daily allowance.
  Each Monid call is bounded at 180 s (`MONID_RUN_DEADLINE_SECONDS`); the worst
  case is 6 calls (2 in the first pass, 2 in each retry pass if more than 10 URLs
  failed) plus 10 s of sleeps, about 1,090 s against the runner's 1,200 s agent
  timeout. A normal run takes about a minute; the worst case is thin and only
  happens when Monid itself hangs on every call.
- The stage counts that explain an empty @github-radar run (`momentum stages: ...`,
  `corroboration kept ...`) are printed, not logged: the agents configure no log
  handler, so `logging.info` never reaches the CI log.
