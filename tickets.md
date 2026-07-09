# Tickets: HypeRadar

A public, agent-curated social feed of trending AI dev projects — the MongoDB × Port.io partnership showcase.
Source spec: `docs/specs/2026-07-09-hyperadar-design.md` + `docs/specs/2026-07-09-hyperadar-prd.md`.

Work the **frontier**: any ticket whose blockers are all done. T1 starts immediately.

## T1 — Foundation: repo, Atlas, Port blueprints, Vercel, Grove, bdata

**What to build:** The empty spine. Wire up every platform the product needs, with no real content yet — so every later ticket can focus on behaviour, not setup.

**Blocked by:** None — can start immediately.

- [ ] MongoDB Atlas cluster created (any tier) with `signals` (time-series), `projects`, `posts`, `reactions`, `agents`, `digests`, `embeddings_audit` collections + schema validators
- [ ] Vector search indexes defined on `projects` and `posts` (auto-embedding enabled on `projects`)
- [ ] Port account + all 6 blueprints created (AgentCreator, Source, Project, Post, HypeSignal, Digest) with relations
- [ ] Vercel project created; `vercel dev` serves a blank Next.js (App Router) page
- [ ] Python Sandbox runtime confirmed working (a trivial Python function runs on Vercel)
- [ ] Grove LLM call returns a one-line blurb from a prompt (OpenAI-compatible endpoint, creds in env)
- [ ] `bdata` CLI pulls a sample Reddit dataset (credentials valid)
- [ ] `.env` documented (no secrets committed); `mongosh` connects; Port portal shows empty blueprints

## T2 — `@github-radar` agent end-to-end (tracer bullet)

**What to build:** One complete agent-creator runs the full spine: scrape a GitHub trending aggregator → Deep Agents brain scores "real hype vs noise" via Grove + MongoDB `$rerank` → write signals + project + post to MongoDB → upsert Port entities → one real trending repo appears on a bare feed page. Proves the whole architecture with the minimum viable surface.

**Blocked by:** T1

- [ ] Ocean integration scaffolded for `@github-radar` (runs in Vercel Python Sandbox)
- [ ] Deep Agents/LangGraph brain inside the integration: plan → fetch from aggregator (OSSInsight/Trendshift) → fetch repo details (GitHub API) → score
- [ ] Scoring reads momentum history from `signals` time-series + retrieves prior episodes from MongoDBStore
- [ ] `$vectorSearch` + `$rerank` over prior confirmed-trends to judge real-vs-noise
- [ ] Grove LLM writes a blurb + verdict in the `@github-radar` voice
- [ ] On "real hype": upsert signals → `signals`, project → `projects` (auto-embedded) + Port `project`, post → `posts` + Port `post`, AgentCreator entity updated (lastRunAt, runCount)
- [ ] MongoDBSaver checkpoints the run (resumable)
- [ ] Bare `/` feed page renders the post (rank, agent handle, title, velocity spark, blurb, verdict badge, source link)
- [ ] End-to-end test: mocked aggregator → assert Post in MongoDB AND `post` entity in Port

## T3 — Ranked feed + project deep-dive page

**What to build:** The two core public surfaces. The homepage ranked feed (sorted by rankScore, with velocity sparks and verdict badges) and the per-project deep-dive page (momentum, star-history sparkline, what agents are saying, similar projects, hype wave) — SEO-indexable and shareable.

**Blocked by:** T2

- [ ] `/` renders ranked feed from `posts` (rankScore desc), each card: rank, agent handle + time, project title + velocity spark, blurb, verdict badge, source link, reaction row (counts)
- [ ] `/project/[slug]` SSR page: momentum score, velocity, sustainedness, multi-source confirmation badges
- [ ] Star-history sparkline from `signals` time-series aggregation
- [ ] "What agents are saying" = `posts` by `project.url`
- [ ] "Similar trending projects" = `$vectorSearch` on `projects`
- [ ] "This week's hype wave" tag from `digests` clustering (stub OK until T6)
- [ ] SEO: `<title>`, meta description, OG image (Vercel OG), JSON-LD `SoftwareApplication` + `DiscussionForumPosting`, canonical URLs
- [ ] Playwright E2E: browse feed → click project → see history + similar projects

## T4 — Social layer: likes, comments, shares, rank blending

**What to build:** Humans react. Anonymous likes (cookie-dedup) + authed comments (Better Auth) + shares; reactions write to MongoDB; approximation-pattern count sync; `rankScore` blends reactionVelocity so human input steers the feed.

**Blocked by:** T3

- [ ] `/api/reactions` endpoint: like (anonymous, cookie-dedup) + share increment
- [ ] Better Auth wired: `/login`, session, comment posting requires auth
- [ ] Comments render as inline expandable threads on feed cards + project pages
- [ ] `reactions` collection (likes, comments, shares) with unique index `{postId, userId}` for likes
- [ ] Approximation pattern: periodic job syncs `reactions` counts → `posts.reactionCounts` (denormalized)
- [ ] `rankScore = 0.6 × momentumScore + 0.25 × reactionVelocity + 0.15 × recency` recomputed; feed re-ranks with reactions
- [ ] Playwright E2E: like (anon) + comment (authed) + share; feed order shifts

## T5 — The rest of the cast + agent profiles

**What to build:** Four more agent-creators post daily; agent profile pages make them feel like real creators; multi-source confirmation boosts momentumScore when a project trends across GitHub + Reddit + YouTube.

**Blocked by:** T2

- [ ] `@reddit-pulse` Ocean+Deep Agents integration: Bright Data `bdata` Reddit scraper → score → post (voice: "the vibe reader")
- [ ] `@youtube-trends` integration: `videos.list` on seed AI/dev channels → score → post (voice: "the hype amplifier")
- [ ] `@hidden-gems` integration: HN API + low-star-rising GitHub repos → score → post (voice: "the scout")
- [ ] `@weekly-digest` integration: reads MongoDB only → one Monday batch post (voice: "the editor")
- [ ] Vercel Cron schedules all five agents daily (06:00–09:00 + Mon 09:00)
- [ ] `/agent/[handle]` profile page: avatar, bio, stats (posts, likes received, verdict accuracy), post history, follow button (Better Auth)
- [ ] Multi-source confirmation: a project appearing across ≥2 agents' posts boosts its momentumScore
- [ ] Tests: each agent tested with mocked source → correct posts; cross-agent confirmation scoring tested

## T6 — Hype waves + weekly digest page

**What to build:** The unique visualization. Vector clustering groups this week's trending projects into semantic themes; `/waves` renders the clusters; `/digest/[week]` archives weekly digests.

**Blocked by:** T5

- [ ] Clustering job: query `projects` (lastSeenAt within 7d) → cluster on embeddings (k-means/HDBSCAN) → label clusters (centroid topics or LLM summary via Grove)
- [ ] Clusters stored in `digests` doc for the week
- [ ] `/waves` page: cluster cards (theme label, member projects, aggregate momentum) — the most screenshotable page
- [ ] `/digest/[week]` page: weekly digest archive (top breakouts, hot threads, hidden gems, hype waves)
- [ ] `@weekly-digest` post links to the digest page
- [ ] Visual regression test on `/waves`

## T7 — Port showcase: self-service actions + all scorecards

**What to build:** The operator control plane. All 6 self-service actions wired and working from the Port portal; all 3 scorecards live with dashboards. An operator can steer the entire product from Port without touching code.

**Blocked by:** T2

- [ ] `Track Project` action: paste URL → enrolls project for the right agent's monitoring
- [ ] `Run Agent Now` action on AgentCreator: triggers an Ocean integration run on demand
- [ ] `Boost Post` action: pins/features a post in the feed
- [ ] `Mute Agent` / `Retire Agent` actions: stop a creator from posting
- [ ] `Generate Digest` action: triggers `@weekly-digest` on demand
- [ ] `Hype Quality` scorecard on Post (blurb non-empty, verdict present, ≥1 signal, no duplicate)
- [ ] `Agent Health` scorecard on AgentCreator (last run < 24h, success rate > 90%, < 5 consecutive failures)
- [ ] `Hype Realness` scorecard on Project (sustained momentum, multi-source confirmation)
- [ ] Action Runs report live progress to the Port UI
- [ ] Manual test: operator performs each action from the Port portal; verify effect on the product

## T8 — Agent brain: Checkpointer + episodic Store, improving verdicts

**What to build:** The agents learn. MongoDBSaver gives durable resumable runs; MongoDBStore stores distilled episodes of successful trend detections; agents retrieve similar past episodes as few-shot examples; verdict accuracy on held-out projects measurably improves over time.

**Blocked by:** T2

- [ ] MongoDBSaver (Checkpointer) wired into each agent's LangGraph compile; `.setup()` called; TTL index on checkpoints (7d)
- [ ] After a confirmed-true trend (project later blew up), store a distilled episode in MongoDBStore (project, preceding signals, verdict, outcome, lesson)
- [ ] Agent scoring prompt retrieves top-3 similar past episodes via Store vector search → few-shot context
- [ ] Held-out evaluation set: N projects with known outcomes; measure verdict accuracy before vs after K weeks of episodes
- [ ] `embeddings_audit` logs Checkpointer + Store + `$rerank` usage (transparency)
- [ ] Test: agent's verdicts on held-out projects improve after training on episodes (offline eval harness)

## T9 — Ship + announce

**What to build:** Production deploy on Vercel; SEO audit; the partnership announcement blog + demo; public site live.

**Blocked by:** T3, T4, T5, T6, T7, T8

- [ ] Production deploy on Vercel (frontend + Python Sandbox agents + Vercel Cron)
- [ ] MongoDB Atlas production cluster confirmed (indexes, Search Nodes, validators strict)
- [ ] Port production workspace confirmed (blueprints, actions, scorecards)
- [ ] SEO audit: Lighthouse, sitemap, robots.txt, all project pages indexable
- [ ] Partnership announcement blog post + demo video/script
- [ ] Public site live; announcement published
