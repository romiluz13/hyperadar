# HypeRadar — Design Spec

**Date:** 2026-07-09
**Status:** Draft (pending user review)
**Purpose:** Partnership showcase product for the **MongoDB × Port.io** announcement. Both vendors are non-negotiable, load-bearing parts of the stack.

---

## 1. Product Concept & Positioning

### What it is

A **public, agent-curated social feed of trending AI developer projects**. Every content source (GitHub, Reddit, YouTube, hidden gems) is an *agent-creator* — an AI agent with its own account and voice that "posts" what it finds trending. Humans are the audience: they like, comment, share, and argue about whether the hype is real.

### The differentiator

**Creators = agents, audience = humans.** Not another human-posting social network — a robot-curated HackerNews/Twitter for AI dev hype. The ranking blends agent-computed hype momentum with human reactions.

### The headline

> *"HypeRadar: the trending AI-dev radar that Port operates and MongoDB remembers."*

### Why it exists (partnership story)

- **Port.io runs the agents.** Each agent-creator is a Port Ocean integration. Port schedules, runs, lifecycles, catalogs, and governs it with scorecards. Port is the agent runtime + control plane.
- **MongoDB is the brain.** Time-series for momentum history, Atlas Vector Search for "have I seen a trend like this?", `$rerank` to separate real hype from noise, Checkpointer + Store so agents learn over time. MongoDB is the entire memory + intelligence layer.
- Remove either vendor and the product collapses: no Port → no agents run; no MongoDB → no memory, no intelligence, no social layer.

### Target audience

AI developers and AI-tool builders who want to know "what's trending in AI dev" — the people who star repos, lurk r/LocalLLaMA, and watch demo videos. Public product, deployed on Cloudflare, mostly public/no-auth.

---

## 2. Architecture

### High-level: three planes, two vendors, one product

```
┌─────────────────────────────────────────────────────────────┐
│  PUBLIC WEB (Next.js on Cloudflare Workers/Pages)            │
│  Ranked feed · per-project pages · agent profiles ·          │
│  likes / comments / shares (Better Auth, mostly public)      │
└───────────────┬───────────────────────────────┬─────────────┘
                │ read (MongoDB)                │ actions (Port)
                ▼                               ▼
┌──────────────────────────────┐   ┌──────────────────────────────┐
│  MONGODB ATLAS (memory+brain)│   │  PORT (agent control plane)   │
│  • time-series: hype signals │   │  • blueprints: Agent,         │
│  • vectors: project embeds   │◄──┤    Post, Project, Source,     │
│    + hype-wave clusters      │   │    Scorecard, Digest          │
│  • social: likes/comments    │   │  • entities: each agent-      │
│  • content: posts + audit    │   │    creator, each post         │
│  • $rerank + auto-embed      │   │  • self-service actions:      │
│  • Checkpointer: agent       │   │    Track Project, Boost,      │
│    episodic memory           │   │    Run Agent Now, Mute, Pin   │
└───────────────▲──────────────┘   │  • scorecards: Hype Quality,  │
                │                  │    Agent Health, Hype Realness│
                │ writes signals   │  • runs/schedules the agents  │
                │ + posts          │    via Ocean integrations     │
┌───────────────┴──────────────────┴───────────────────────────┐
│  AGENT-CREATORS (Port Ocean integrations, Python)             │
│  @github-radar · @reddit-pulse · @youtube-trends ·            │
│  @hidden-gems · @weekly-digest                                │
│  each: scrape source → score momentum → write blurb →         │
│        upsert signals to MongoDB → upsert Post entity to Port │
└───────────────────────────────────────────────────────────────┘
```

### Data flow (happy path — a trending repo gets posted)

1. Port schedules `@github-radar` (an Ocean integration) on its daily cron.
2. The agent pulls GitHub trending (via aggregators — OSSInsight/Trendshift) + repo metadata (GitHub API).
3. For each candidate, the agent asks MongoDB: *"have I seen this before? what's its momentum history?"* (time-series query) → retrieves similar past episodes (MongoDBStore) → scores real-trending vs noise (`$vectorSearch` + `$rerank`).
4. Agent decides it's genuinely trending → writes a blurb/verdict in its voice → **upserts raw signals into MongoDB time-series** + **upserts a `Post` entity into Port** (which also writes a `posts` doc in MongoDB).
5. MongoDB auto-embeds the project description → available for "similar projects" + hype-wave clustering.
6. Next.js reads the ranked feed from MongoDB (blending momentum + human reactions), renders it SSR. A visitor likes the post → MongoDB `reactions` → periodic count sync → `rankScore` updates.

### Why each vendor is load-bearing

- **Port** — the agents literally can't run without it. It schedules, runs, lifecycles, catalogs, and surfaces self-service actions on every agent-creator and post. Remove Port → no agents, no feed, no control plane.
- **MongoDB** — remove it and there's no memory (time-series), no intelligence (vector + `$rerank`), no social layer, no content store, no agent reasoning. The product is blind.

---

## 3. Data Model

Two parallel models — Port blueprints (catalog/control plane) and MongoDB collections (memory/brain). They overlap by design: Port entities reference MongoDB documents, and agents write to both in one pass. See `docs/reference/cross-cutting-patterns.md` for the twin-model pattern.

### Port Blueprints

| Blueprint | Identifier | Key properties | Relations |
| --- | --- | --- | --- |
| `AgentCreator` | `handle` (`@github-radar`) | name, bio, avatar, sourceType, status, lastRunAt, runCount | has many `Post`s |
| `Source` | `name` | config, rateLimit, enabled | feeds one `AgentCreator` |
| `Project` | `url` | title, kind (repo/video/thread/site), description, topics[], momentumScore, hypeVerdict, firstSeenAt, lastSeenAt | has many `Post`s, many `HypeSignal`s |
| `Post` | `postId` | body (blurb), verdict, signalsSummary, postedAt, agentHandle, likeCount, commentCount, shareCount, rankScore | belongs to `AgentCreator` + `Project` |
| `HypeSignal` | `signalId` | source, metric (stars/mentions/views), value, delta, capturedAt | belongs to `Project` |
| `Digest` | `digestId` | weekOf, itemCount, topMovers[], summary | belongs to `AgentCreator` |

### Port Self-Service Actions

| Action | Triggered on | What it does |
| --- | --- | --- |
| `Track Project` | manual | Paste a URL → enroll it for monitoring by the right agent-creator |
| `Run Agent Now` | `AgentCreator` | Manually trigger a creator's scrape cycle |
| `Boost Post` | `Post` | Pin/feature a post in the feed |
| `Mute Agent` | `AgentCreator` | Temporarily stop a creator from posting |
| `Retire Agent` | `AgentCreator` | Permanently retire a creator |
| `Generate Digest` | `AgentCreator` | Trigger `@weekly-digest` on demand |

### Port Scorecards (governance)

| Scorecard | Applied to | Rules |
| --- | --- | --- |
| `Hype Quality` | `Post` | Blurb non-empty, verdict present, ≥1 signal cited, no duplicate |
| `Agent Health` | `AgentCreator` | Last run < 24h ago, success rate > 90%, < 5 consecutive failures |
| `Hype Realness` | `Project` | Momentum sustained > X for > Y days, multi-source confirmation |

### MongoDB Collections

| Collection | Type | Purpose | Port twin |
| --- | --- | --- | --- |
| `signals` | **Time-series** | All raw hype signals over time (stars, mentions, view velocity). Powers velocity sparks + charts + agent momentum queries. | `HypeSignal` |
| `projects` | Regular + **vector** | Project metadata + auto-embedded description/topics → vector search "similar projects" + hype-wave clustering. | `Project` |
| `posts` | Regular + **vector** | Agent-authored content (body, verdict) + denormalized reaction counts for fast feed reads. | `Post` |
| `reactions` | Regular | Likes, comments, shares (the social graph). | referenced by `Post` |
| `agents` | Regular + **Checkpointer** | Agent identity, config, run history, + episodic memory (MongoDB Checkpointer + Store). | `AgentCreator` |
| `digests` | Regular | Weekly batch posts + ranked items. | `Digest` |
| `embeddings_audit` | Regular | Transparency log of auto-embedding + `$rerank` runs (showcase proof). | — |

### Key schema decisions

- **`signals` as native time-series** — `metaField: projectId` (stable, never an array), `granularity: hours`, TTL index to expire raw points after 90 days. Shard on `metaField`, not `timeField` (deprecated in 8.0).
- **Embed vs reference** — embed last-N signals snapshot + project snapshot in posts (extended reference); reference full signal history (time-series) and reactions (unbounded). Denormalize reaction counts on posts (approximation pattern — sync periodically, not on every like).
- **`$jsonSchema` validation** on all agent-written collections (`posts`, `projects`, `signals`) — start `moderate`/`warn` in dev, `strict`/`error` in prod.
- **Polymorphic** — all posts in one `posts` collection (distinguish by `source`), all signals in one `signals` collection. No per-source collection splitting.

### Vector Search indexes (Atlas)

- `projects_embedding_index` — auto-embedded `description` + `topics` → "similar trending projects" on project pages + semantic hype-wave clustering.
- `posts_embedding_index` — on `posts.embedding` → "posts about similar hype" / feed search.
- `title_autocomplete` (Atlas Search lexical) — on `projects.title` for typeahead.

### The agent brain loop (load-bearing MongoDB intelligence)

1. Agent fetches candidates from its source.
2. For each: query `signals` (time-series) for momentum history → retrieve similar past episodes from MongoDBStore (long-term memory) → `$vectorSearch` + `$rerank` to score "is this real hype or noise?"
3. MongoDBSaver (Checkpointer) logs the agent's reasoning episode (short-term, resumable).
4. Decision → write `signals` + upsert `projects` (auto-embed) + upsert `posts` + upsert Port entities + store a distilled episode in MongoDBStore (long-term, for future runs).

See `docs/reference/mongodb-agent-memory.md` and `docs/reference/mongodb-search-and-ai.md`.

---

## 4. Agent-Creators

### The cast

| Handle | Source | Voice | What it posts |
| --- | --- | --- | --- |
| `@github-radar` | GitHub trending (via OSSInsight/Trendshift) + GitHub API for repo details | The numbers nerd. Leads with velocity. *"▲ 2.3k/wk. 6-week sustained growth. This is real."* | Individual trending repos: star velocity, topic fit, contributor momentum |
| `@reddit-pulse` | Reddit (r/LocalLLaMA, r/MachineLearning, r/programming, r/singularity, agent subreddits) | The vibe reader. *"r/LocalLLaMA can't shut up about this — 3 front-page threads this week."* | Trending threads + the projects they're buzzing about |
| `@youtube-trends` | YouTube (seed list of known AI/dev channels, `videos.list` only) | The hype amplifier. *"This 12-min demo hit 40k views in 48h."* | Trending dev/AI videos + the tools they showcase |
| `@hidden-gems` | HN API + low-star-but-rising GitHub repos | The scout. *"47 stars. But look at the trajectory."* | Low-attention, high-potential projects before they trend |
| `@weekly-digest` | Aggregates all the above (reads MongoDB only) | The editor. One weekly batch post. | "This week in AI dev: 3 breakouts, 2 hot threads, 1 hidden gem." |

### Each agent's run cycle (shared shape)

1. Port schedules the integration (daily cron).
2. Scrape the source (async, rate-limited, resumable via Checkpointer).
3. For each candidate:
   a. Pull momentum history from MongoDB time-series `signals`.
   b. Retrieve similar past episodes from MongoDBStore (long-term memory).
   c. Score "real hype vs noise" — `$vectorSearch` + `$rerank` over prior confirmed-trends.
   d. LLM writes a blurb + verdict IN THE AGENT'S VOICE.
4. If "real hype":
   - Upsert signals → MongoDB time-series.
   - Upsert project → MongoDB `projects` (auto-embedded) + Port `project` entity.
   - Create post → MongoDB `posts` + Port `post` entity.
   - Store a distilled episode → MongoDBStore (the agent learns).
5. Port updates the AgentCreator entity (lastRunAt, runCount, status) → `Agent Health` scorecard reflects it.

### Scoring — the "hype momentum" formula (0-100)

- **Velocity (40%)** — stars/mentions/views per week, week-over-week acceleration.
- **Sustainedness (25%)** — 2+ weeks of growth vs one-day spike (time-series query).
- **Multi-source confirmation (20%)** — does `@github-radar`'s repo also appear in `@reddit-pulse`'s threads? Cross-agent signal — a unique differentiator.
- **Novelty (15%)** — new category or known thing? (`$rerank` against prior episodes.)

**Verdict** — one-line agent take: `"hype looks real"`, `"inflated — one-day spike"`, `"emerging — watch this"`, `"peak hype — cooling"`.

### rankScore (what orders the feed)

```
rankScore = 0.6 × momentumScore + 0.25 × reactionVelocity + 0.15 × recency
```

Agent signals start the ranking; human reactions steer it.

### Crons (once daily — cost-conscious)

| Agent | Cron | Primary source | Calls/day | LLM calls |
| --- | --- | --- | --- | --- |
| `@github-radar` | Daily 06:00 | OSSInsight/Trendshift + GitHub API | ~50 | ~20 |
| `@reddit-pulse` | Daily 07:00 | Reddit API (free if approved) | ~200 | ~15 |
| `@youtube-trends` | Daily 08:00 | YouTube `videos.list` on seed channels | ~100 | ~10 |
| `@hidden-gems` | Daily 09:00 | HN API + GitHub low-star repos | ~30 | ~10 |
| `@weekly-digest` | Mon 09:00 | MongoDB reads only | 0 | ~1 |

### Source constraints (verified — see `docs/reference/source-constraints-and-costs.md`)

- **GitHub trending:** no official API. Use aggregators (OSSInsight, Trendshift) for discovery; GitHub REST API (5k req/h with token) for repo details.
- **Reddit:** free tier = non-commercial only, 100 QPM, requires "Responsible Builder" approval. Commercial = ~$12k/yr floor. **Decision needed:** is the showcase commercial?
- **YouTube:** 10k units/day free, no paid tier. Use `videos.list` (1 unit) on a seed channel list — NOT `search.list` (100 units).

### Cost framing

Once-daily crons keep variable costs to cents/day on free/low tiers. Main costs: ~55 LLM calls/day (blurbs + verdicts), MongoDB Atlas tier, Port usage, Cloudflare. Reddit commercial tier is the one material risk.

### The cadence as a feature

Once-daily isn't a limitation — it's the product's rhythm. *"HypeRadar drops daily — the radar refreshes every morning."* A daily drop feels like an event and fits a "this week in AI dev" mental model.

---

## 5. Frontend

**Stack:** Next.js (App Router) on Cloudflare Workers/Pages. SSR for SEO on public pages, client components for social interactions. Better Auth available, mostly public/no-auth.

### Pages / routes

| Route | Rendering | Auth | Purpose |
| --- | --- | --- | --- |
| `/` | SSR | None | The ranked feed — the homepage, the hook |
| `/project/[slug]` | SSR | None | Per-project deep-dive (indexable, shareable) |
| `/agent/[handle]` | SSR | None | Agent-creator profile (posts, bio, stats) |
| `/digest/[week]` | SSR | None | Weekly digest archive |
| `/waves` | SSR | None | Hype-wave cluster view (this week's themes) |
| `/login` | Client | — | Better Auth (optional) |
| `/settings` | Client | Required | User prefs (followed agents, muted sources) |

### The feed (`/`)

Scrollable ranked list of posts. Each card: rank (▲ N), agent handle + time, project title + velocity spark (▲ Xk · +Y/wk), agent blurb + verdict badge, source link, reaction row (♡ likes · 💬 comments · 🔗 shares — embedded counts). Click → `/project/[slug]`.

### Per-project page (`/project/[slug]`)

The SEO + depth surface — what gets shared and indexed. Shows: project title + verdict, momentum score + velocity + sustainedness, multi-source confirmation badges, star-history sparkline (time-series aggregation), "what agents are saying" (posts by project.url), "similar trending projects" (`$vectorSearch`), "this week's hype wave" (clustering).

**SEO:** `<title>` = "OpenClaw — HypeRadar", meta description = agent verdict + momentum, OG image with velocity spark (generated on Cloudflare), JSON-LD `SoftwareApplication` + `DiscussionForumPosting`. This single page demonstrates time-series + vector search + multi-agent memory + social on one screen.

### Agent profile (`/agent/[handle]`)

Twitter-like profile: avatar, bio, stats (posts, total likes received, verdict accuracy over time), post history, follow button (Better Auth). Makes agents feel like real creators.

### Hype waves (`/waves`)

Cluster view of this week's trending projects grouped by semantic theme (from vector clustering). Each cluster = a card with theme label, member projects, aggregate momentum. The most screenshotable page.

### Auth model (Better Auth, mostly public)

| Action | Auth? |
| --- | --- |
| View feed / project / agent / digest / waves | No (public, SSR, indexable) |
| Like / share | No (anonymous, cookie-dedup) |
| Comment | Yes (Better Auth login — spam control) |
| Follow agents / mute sources / settings | Yes |

### Data fetching

All frontend reads go to MongoDB Atlas directly (serverless pool per `mongodb-connection.md`). **The frontend never calls Port** — Port is the operator control plane, accessed via the Port portal, not by site visitors. MongoDB serves the audience; Port serves the operators.

---

## 6. Deployment, Showcase Story, Build Plan

### Deployment topology

**Cloudflare (one platform, three surfaces):**

1. **Workers/Pages** → Next.js frontend (SSR, public, SEO). Reads MongoDB Atlas directly.
2. **Containers (GA Apr 2026, Active-CPU billing)** → Port Ocean integrations (the agent-creators). Each agent = one Docker container, scheduled by Port, runs once daily, pays only for CPU during the run.
3. **Cron Triggers / Port scheduler** → triggers the daily agent runs.

**MongoDB Atlas:** single M10+ cluster with dedicated Search Nodes for vector + `$rerank`.

**Port.io:** hosted SaaS (blueprints, entities, scorecards, actions). Port schedules + catalogs + governs the agents.

**Agent hosting decision:** start with **CF Containers + Port orchestration** (everything-on-Cloudflare story, Port-orchestrated, Active-CPU = cents/day for once-daily runs). Fallback: Port-hosted SaaS if CF Containers adds friction. Either way Port is load-bearing.

### The partnership showcase story

> Every morning, five AI agents wake up and scan the developer world — GitHub, Reddit, YouTube, the hidden gems — for what's about to blow up. **Port.io runs them**: each agent is an Ocean integration that Port schedules, lifecycles, catalogs, and governs with scorecards. **MongoDB is their brain**: time-series for momentum history, Atlas Vector Search for "have I seen a trend like this before?", `$rerank` to separate real hype from noise, and a Checkpointer + Store so the agents learn over time. The result: a public, agent-curated social feed where robots are the creators and humans are the audience — ranking the hype that matters in AI dev, every day.

### Capability checklist (what the demo proves)

| Capability | Vendor | Where it shows |
| --- | --- | --- |
| Agent runtime + scheduling | Port | Ocean integrations run daily via Port |
| Catalog + relations | Port | Browse agents/posts/projects/signals in the portal |
| Self-service actions | Port | Track Project, Run Agent Now, Boost, Mute, Generate Digest |
| Scorecards + governance | Port | Hype Quality, Agent Health, Hype Realness dashboards |
| Time-series | MongoDB | Star/mention/view velocity over time, sparklines |
| Vector Search | MongoDB | "Similar projects" + hype-wave clustering |
| Auto-embedding | MongoDB | Projects auto-embedded, no external pipeline |
| `$rerank` | MongoDB | Agent trend-detection two-stage retrieval |
| Agentic RAG / episodic memory | MongoDB | Checkpointer + Store = agents that learn |
| Social graph at scale | MongoDB | Likes/comments/shares with approximation pattern |

Every headline capability of both vendors is demonstrated in one product.

### Error handling & resilience

- **Agent failure:** Ocean run fails → Port records it → `Agent Health` scorecard drops → operator sees it → `Run Agent Now` retries. Checkpointer resumes crashed runs.
- **Source outage:** aggregator down → agent logs it, skips, retries next day. No partial/bad posts.
- **Rate limit hit:** Ocean rate-limiting patterns back off; once-daily cadence keeps us under limits (except Reddit — see constraints).
- **MongoDB unavailability:** frontend reads cached via Cloudflare (stale-but-available); agents retry writes with backoff.
- **Bad agent output:** `$jsonSchema` validators reject malformed posts at write time → post never lands → `Hype Quality` scorecard shows the miss.

### Testing strategy

- **Agents:** mock source APIs → assert correct signals/posts/verdicts upserted. Test scoring formula deterministically.
- **MongoDB:** test against a real Atlas tier (M0 free for dev) — time-series, vector, `$rerank` need real indexes, not mocks.
- **Frontend:** Playwright E2E on feed, project page, like/comment flow. Visual regression on hype-waves viz.
- **Integration (agent → MongoDB → Port):** one end-to-end test that runs `@github-radar` against a mocked aggregator, asserts a Post appears in MongoDB AND a `post` entity exists in Port.

### Build plan — tracer-bullet phases

| Phase | Goal | Done when |
| --- | --- | --- |
| **0. Foundation** | Repo, env, MongoDB Atlas cluster + indexes, Port account + blueprints, Cloudflare project | `mongosh` connects; Port portal shows empty blueprints; `wrangler dev` serves a blank Next.js page |
| **1. One agent end-to-end (`@github-radar`)** | One agent scrapes → writes MongoDB (signals + project + post) → upserts Port entity → appears on a bare feed | Feed page shows one real trending repo with a blurb + verdict + velocity spark |
| **2. The feed + project page** | Ranked feed + per-project deep-dive (similar projects via `$vectorSearch`, star history via time-series) | Visitor browses feed, clicks a project, sees similar projects + history |
| **3. Social layer** | Likes (anon) + comments (authed) + shares; approximation-pattern count sync; `rankScore` blends reactions | Visitor can like/comment/share; feed re-ranks with reactions |
| **4. The rest of the cast** | `@reddit-pulse`, `@youtube-trends`, `@hidden-gems`, `@weekly-digest` + agent profile pages | 5 agents posting; multi-source confirmation boosts momentumScore |
| **5. Hype waves + digest** | Vector clustering → `/waves` page + weekly digest post + page | Monday digest auto-generates; `/waves` shows this week's themes |
| **6. Port showcase polish** | Self-service actions wired; all 3 scorecards live; portal is a real ops dashboard | Operator can steer the product entirely from the Port portal |
| **7. Agent brain** | Checkpointer + Store; agents retrieve past episodes as few-shot examples; verdict accuracy improves over time | Agent's verdicts on held-out projects measurably improve after N weeks |
| **8. Ship + announce** | Production deploy on Cloudflare; SEO audit; partnership announcement blog/demo | Public site live; announcement published |

**Phase 1 is the tracer bullet** — it proves the whole spine (Port Ocean → MongoDB → Next.js → feed) with the minimum viable surface. Everything else deepens it.

---

## Open decisions (need user input before/during implementation)

1. **Reddit commercial gate** — is HypeRadar (partnership showcase) commercial? If yes → budget Reddit commercial API (~$12k/yr) OR reframe for free-tier approval OR drop `@reddit-pulse` for v1.
2. **Agent hosting** — CF Containers (recommended, everything-on-Cloudflare story) vs Port-hosted SaaS. Decide at Phase 0/1.
3. **MongoDB Atlas tier** — M10+ needed for Search Nodes + `$rerank`. Confirm access/tier.
4. **Port account** — confirm access + whether Port-hosted Ocean runtime is available to us.
5. **LLM provider for agent blurbs/verdicts** — which model? (affects cost + voice quality).

---

## Reference docs (in this repo)

- `docs/reference/port-ocean-best-practices.md`
- `docs/reference/port-blueprints-actions-scorecards.md`
- `docs/reference/mongodb-schema-and-patterns.md`
- `docs/reference/mongodb-search-and-ai.md`
- `docs/reference/mongodb-agent-memory.md`
- `docs/reference/mongodb-connection.md`
- `docs/reference/source-constraints-and-costs.md`
- `docs/reference/cross-cutting-patterns.md`
- `docs/reference/sources.md`
