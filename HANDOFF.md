# HANDOFF — Hyperadar Deep Review + Port Showcase Pivot

> **Superseded review snapshot.** This file records findings from before the
> retry-safe publication and Port Workflow work landed. Do not use it as
> current runtime truth. Start with `README.md` and `docs/README.md`. The fake
> Vercel agent routes, webhook actions, and Vercel cron configuration described
> below were retired; the active execution path is Port Workflow → GitHub
> Actions → an isolated, frozen Python agent package.

**Date:** 2026-07-13  
**Branch:** `main` (tracks `origin/main`)  
**Workspace:** `/Users/rom.iluz/Dev/hyperadar`  
**Prior chat:** Hyperadar Deep Multi-Agent Review (Cursor Grok 4.5, 9 parallel subagents)

## Purpose for the next session

Continue from a full-codebase multi-agent review and a Port.io feature-research pass. User is **Port.io** (partnership showcase with MongoDB). Goal: make Port’s half of the demo load-bearing and showcase Port’s **newest / most valuable 2026 features** (not just Ocean/catalog theater). Do **not** invent product purpose — keep showcase context in agent memory; user asked not to write partnership narrative into random files beyond this handoff / existing docs.

---

## Product context (do not lose)

- **App:** HypeRadar — public agent-curated social feed of trending AI-dev projects.
- **Partnership:** MongoDB × Port.io collaboration showcase.
- **Tagline:** “The trending AI-dev radar that Port operates and MongoDB remembers.”
- **Stack (claimed):** Next.js on Vercel, Port (catalog/control), Deep Agents/LangGraph, Grove LLM, MongoDB Atlas (TS + vector + checkpointer), Bright Data for Reddit.
- **Creators:** `@github-radar`, `@reddit-pulse`, `@youtube-trends`, `@hidden-gems`, `@weekly-digest`.
- **Canonical specs (read these, don’t rewrite):**
  - `docs/specs/2026-07-09-hyperadar-prd.md`
  - `docs/specs/2026-07-09-hyperadar-design.md`
  - `docs/announcement.md`
  - `docs/reference/*` (Mongo + Port patterns)
  - `tickets.md` (all unchecked in git; code is ahead of tickets)

---

## Repo layout (source of truth)

```
apps/web/           # Next.js SSR feed/project/waves/digest/agent + APIs
integrations/       # 5 Python agents + _shared twin-write spine
scripts/            # MongoDB, Port catalog, and Port Workflow provisioning
docs/               # specs + reference
```

**Local dirty files at review time (may still be WIP):**  
`apps/web/app/globals.css`, `apps/web/app/project/[slug]/page.tsx`, `apps/web/app/waves/page.tsx`

**Secrets:** `.env` exists locally and is gitignored. Do not commit or echo secrets. `.env.example` is incomplete vs runtime (`GITHUB_TOKEN`, `CRON_SECRET` missing; Better Auth vars are aspirational).

---

## Overall verdict (9-agent consensus)

**Strong blueprint, half-built proof. MongoDB is mostly load-bearing; Port is mostly a catalog mirror with stubbed control-plane actions.**

| Collapse test | True in code? |
|---|---|
| Remove MongoDB → product dies | **Yes** |
| Remove Port → product dies | **No** (agents via `scripts/daily_run.sh`; feed reads Mongo) |

**Do not invite both companies to a live joint demo until P0 Port control-plane work lands** (or rewrite announcement to “Port governs / MongoDB remembers” and stop claiming runtime).

### Subagent IDs (resume if needed)

| Lens | Agent ID |
|---|---|
| Architecture | `9f58c04c-58df-47a6-9e73-868761c7b0d3` |
| Frontend / UX | `02a65162-696e-411f-adae-71f71c23065b` |
| MongoDB | `3ae6af59-8eb8-419a-b148-7074ccfd9204` |
| Port.io | `9ec65aa5-e11a-4d80-b440-13812fce4ffd` |
| Agents pipeline | `d9a0df6b-2c62-4ece-9deb-62edd14b3fc4` |
| Security | `72f2305b-d650-468c-b31d-23cc618e8035` |
| Performance / reliability | `36f7447c-656c-4133-9fe8-43dfe6825ee0` |
| Testing | `1a29702a-5a76-4c05-b555-ab44f194c051` |
| Showcase narrative | `d367b4d0-54f9-43fa-8c4e-001448f47e6d` |

All ran on **Cursor Grok 4.5** (`grok-4.5-fast-xhigh`).

---

## Cross-cutting P0 bugs (every lens agreed)

1. **`@github-radar` forks `_shared/write_post`** — loses daily dedup, URL scheme guard, episodic stamp, multi-source boost, embeddings_audit. Local duplicates: `integrations/github_radar/{mongo,port_client,embeddings}.py`.
2. **Port actions + Vercel cron do not run agents** — `apps/web/app/api/port/webhook/route.ts` `run_agent_now` only `$set`s `lastRunAt`; `generate_digest` is SUCCESS no-op; `apps/web/app/api/agents/*/route.ts` return JSON stubs. Real runs: `scripts/daily_run.sh` (hardcoded Mac path).
3. **`$rerank` is announcement theater** — zero call sites in `.py`/`.ts`.
4. **Episodic “agents learn” is post-hoc** — `write_post` retrieves episodes *after* LLM verdict; `store_episode` only in seed/tests, not runtime outcome loop. `@github-radar` skips retrieve entirely.
5. **Mute/Retire non-operative** — webhook writes Mongo `agents.status`; `upsert_agent` always forces `status:"active"` + `runCount=0`; runners never gate on mute.
6. **Frontend under-sells vendors** — cream/lime feed OK; agent/digest/Comments still dark leftovers; Atlas/Port capabilities unlabeled in UI.
7. **Better Auth not wired** — `apps/web/lib/auth.ts` is cookie-only; comments ungated; PRD claims login for comments/follows.
8. **`_shared/mongo.py` opens a new Motor client per DB access** — connection leak risk (github_radar local mongo caches correctly).
9. **No CLI/`ainvoke` timeouts** — `bdata` / `yt-dlp` / Deep Agents can hang forever; sequential `daily_run.sh` stalls.
10. **No Playwright, no CI** — pytest/Atlas partial; web E2E absent.

---

## Findings by category (compressed)

### Architecture
- Intended spine is deep: agents → twin write (Mongo brain + Port catalog) → web reads Mongo only.
- `_shared/write_post.py` + `_shared/runner.py` are the right seams; github_radar Phase-1 fork undercuts them.
- Twin incomplete: Port never upserts HypeSignal / Digest / Source; `setup_port.py` creates actions+scorecards only (no blueprint provisioning).
- Digest modeled as fake `Project` with `hyperadar://digest/...` pollutes projects.
- Slug identity triplicated (TS + two Python modules).

### Frontend / UX
- Strong: `/`, `/project/[slug]`, `/waves` cream–lime system; sparkline; vector similar.
- Broken: `/agent/[handle]`, `/digest/[week]`, `Comments.tsx` dark theme fracture.
- Fake UI: feed tabs, rail topics, Follow button.
- SEO: project metadata/OG/JSON-LD gutted; sitemap omits `/project/*` and `/digest/*`.
- Share copies feed URL not project permalink (`ReactionBar`).
- Duplicate `#main-content` (layout + home).
- Pages often minified to few lines — hard to review/maintain.
- Missing `/login`, `/settings`; nav missing Digests/Agents.

### MongoDB (claimed vs real)

| Feature | Status |
|---|---|
| Time-series `signals` | **Real** |
| `$vectorSearch` similar projects | **Real** (silent `catch {}` on failure) |
| Posts vector / feed semantic search | **Missing** |
| Atlas auto-embedding | **Theater** (local MiniLM) |
| `$rerank` | **Theater** |
| MongoDBSaver Checkpointer | **Partial** (new `thread_id` every run → not resumable; `.setup()` never called) |
| MongoDBStore | **Theater** (custom `episodes` collection) |
| Episodic few-shot in LLM | **Theater** (post annotation) |
| Hype waves | **Partial** (in-memory cosine on embeddings, not Atlas clustering) |
| Reaction approximation pattern | **Opposite** (hot `$inc` every reaction) |
| Web pool (`apps/web/lib/mongo.ts`) | **Good** |
| Python `_shared/mongo.py` | **Broken** (new client each call) |
| Digest indexes | Drift: index on `weekOf`, queries use `weekId` / `computedAt` |

### Port.io (as implemented)
- Real: twin upsert agent/project/post; webhook HMAC+replay; Boost can set `rankScore:100`; e2e Port assert in github-radar test.
- Theater: Ocean (zero SDK); Run Agent Now; Generate Digest; cron Sandbox enqueue; scorecards cosmetic; 3/6 blueprints live; reaction counts never sync to Port.
- Doc honesty gap: `integrations/_shared/port_client.py` admits REST not Ocean; `docs/reference/port-ocean-best-practices.md` still markets Ocean load-bearing.

### Agents pipeline
| Agent | Maturity |
|---|---|
| github_radar | Complete tracer, **partial** shared intelligence (bypasses write_post) |
| reddit_pulse | SERP discovery only; engagement unavailable |
| youtube_trends | yt-dlp real; scores **serp_rank**, ignores `viewCount` |
| hidden_gems | HN+GH real; weak AI filter |
| weekly_digest | Aggregator works; waves bolted on |

- Multi-source boost keys on exact `project.url` — agents post GH vs Reddit vs YouTube URLs → confirmation almost never fires.
- Grove + Deep Agents wiring is real across all five.

### Security
- **Strong:** Port webhook HMAC fail-closed.
- **Critical:** `/api/agents/*` unauthenticated (dangerous once stubs become real).
- **High:** anonymous cookie farming for likes → rank abuse; Better Auth absent; Port `track_project` accepts arbitrary URL schemes (XSS/`javascript:`) unlike `write_post` guard.
- Medium: no rate limits/CSP; incomplete `.env.example`.

### Performance / reliability
- Feed query indexed + `limit(20)` OK; every page `force-dynamic` (no ISR).
- Production freshness SPOF = laptop `daily_run.sh`.
- Project page unbounded signals/posts `.toArray()`.
- ReactionBar N+1 (20 posts → 20 GETs).
- Observability ≈ zero (local log file only).

### Testing
- ~30–40% of PRD testing strategy.
- Real Atlas + one github-radar Mongo+Port persistence e2e (skips agent graph/Grove).
- Multi-source / rankScore tests often **tautological** (recompute formula in test, don’t call production path).
- No Playwright, no visual regression for `/waves`, no CI (`.github/` absent).

### Showcase narrative
- Mongo can look good with seeded Atlas.
- Port cannot look indispensable until actions/workflows actually operate agents.
- UI never says MongoDB/Port except meta tagline.
- `tickets.md` all unchecked while substantial code exists — shipping state unclear for announce.

---

## Port feature research (2026 — pivot the showcase)

User asked to search Port docs/web for **newest / most valuable** features because they are Port and need to showcase Port’s full power.

### Port’s current product pillars (docs.port.io)
Context Lake · Workflows & tools · Port AI · Agent management · Interface builder · Governance · Platform admin.

### Highest-ROI Port features for Hyperadar (ranked)

1. **Workflows (Open Beta)** — visual multi-step; triggers: human form, catalog event, **agent tool (MCP)**, schedule. Same audit for humans+AI. → Replace stub actions as primary operator surface.  
   Docs: https://docs.port.io/workflows/overview  
   Release: https://www.port.io/blog/product-release-notes-june-2026
2. **Workflows as MCP agent tools** — agents invoke same governed graph.
3. **Port AI Agents + invoke API** — domain agent + automations + AI Chat widgets.  
   Docs: https://docs.port.io/ai-interfaces/ai-agents/interact-with-ai-agents
4. **Context Lake** — catalog as system of record *and* action.  
   https://www.port.io/platform/context-lake
5. **External MCP on AI agents** (June 2026) — e.g. MongoDB MCP + Port catalog in one chat.
6. **Skills registry + Port MCP** — process playbooks in catalog → Cursor/Claude.  
   https://www.port.io/blog/introducing-skills-in-port
7. **Scorecards → AI self-heal** — degrade → automation → AI remediation.  
   https://docs.port.io/guides/all/self-heal-scorecards-with-ai/
8. **AI registry** (agents, skills, MCPs) — homepage Port story.
9. **Self-service + run logs** — still demo-critical when actually wired.
10. **Private Pages / dashboards + AI Chat** — operator demo UX.
11. **Ocean / Ocean Custom** — secondary now; don’t hang demo on Ocean-as-runtime.
12. **Eng Intelligence (Cursor/Claude/Copilot)** — Port-internal flex; weak for Hyperadar×Mongo narrative.

### Recommended narrative rewrite

> **Port governs and operates the agent control plane. MongoDB is the memory and intelligence layer.**

Stop leading with “every agent IS an Ocean integration” unless Ocean is implemented. Prefer: Context Lake entities + Workflows operate them + Port AI/MCP govern them.

### Suggested Port-first build order

| Step | Ship | Proves |
|---|---|---|
| P0a | Workflows that actually enqueue/run agents + run status/logs | System of action |
| P0b | All blueprints + real scorecards; mute/retire honored | Context Lake + governance |
| P0c | Port AI “Hype Ops” agent + dashboard AI Chat | Agent management |
| P1a | Expose Run Agent / Boost as MCP tools | Governed agent tools |
| P1b | MongoDB MCP (or HTTP) as external MCP on Port AI | June 2026 MCP |
| P1c | Scorecard degrade → automation → Hype Ops | Self-heal |
| P2 | Skills for verdict/digest playbooks | Skills registry |
| P2 | Ocean Custom Mongo→Port sync if Ocean must appear on stage | Ocean Custom |

Mongo keep: time-series, `$vectorSearch`, episodic (once wired pre-LLM), rankScore, waves.

---

## Merged P0 backlog (execute in roughly this order)

1. Make Port **Run Agent Now** / cron / Workflows **actually run** agents (or Port story fails).
2. Collapse `@github-radar` onto `_shared/write_post` + `runner`; delete forked infra.
3. Episodic retrieve **before** LLM verdict; `store_episode` on confirmed outcomes.
4. Auth-gate `/api/agents/*` (`CRON_SECRET`); rate-limit reactions; validate `track_project` URLs.
5. Fix `_shared/mongo.py` client singleton; add timeouts on `bdata`/`yt-dlp`/`ainvoke`.
6. Honor mute/retire end-to-end; stop `upsert_agent` resetting status/runCount.
7. Seed demo corpus + label Atlas/Port in UI.
8. Unify agent/digest/Comments onto cream design; restore project SEO + sitemap.
9. Implement `$rerank` **or** strip from README/announcement/tickets.
10. Playwright + CI.

---

## What NOT to do

- Do not commit `.env` or print secrets.
- Do not claim Ocean runtime / `$rerank` / “agents learn” in demos until wired.
- Do not broaden scope into Eng Intelligence Cursor tracking for the Mongo partnership demo.
- Do not duplicate PRD/design into new long specs — update implementation + optionally tickets/announcement language.
- User said partnership showcase framing should live in agent context; avoid scattering marketing into random source files (UI vendor labels for demo are OK).

---

## Suggested skills (invoke next session)

1. **`implement`** — once a Port-first P0 slice is chosen, implement completely (no stubs).
2. **`wayfinder`** — if splitting Port Workflows + github_radar collapse + UI into parallel workstreams.
3. **`tdd`** — for write_post migration, mute gating, webhook→run path (lock behavior with tests).
4. **`verification-before-completion`** — before claiming Port “operates” is true.
5. **`grill-with-docs` / `grilling`** — if rewriting announcement/PRD Port claims against docs.port.io.
6. **`frontend-design` / `impeccable`** — finishing agent/digest/Comments onto the cream system.
7. **`web-design-guidelines`** — a11y/SEO restore on project pages.
8. **`vercel-react-best-practices`** — ISR/`revalidate`, N+1 ReactionBar, force-dynamic cleanup.
9. **`mongodb-search-and-ai` / `mongodb-query-optimizer`** — if wiring `$rerank` / vector feed search for real.
10. **`code-review`** — after a Port-first PR, standards vs spec.
11. **`deploy-to-vercel`** — when cron/Sandbox enqueue is real.
12. **`to-tickets`** — sync `tickets.md` to reality after plan lock.
13. **`research` / `octocode-research`** — if deepening Port Workflows API / MCP tool exposure specifics.
14. **`skill-router`** — if the next ask is ambiguous across Port vs Mongo vs UI.

---

## Immediate next questions for the human (if needed)

1. Prefer **Workflows Open Beta** as the primary control plane, or finish legacy self-service actions first then migrate?
2. Is Port Ocean required on stage for the partnership, or is Context Lake + Workflows + AI Agents enough?
3. Which P0 slice first: **(A)** real Run Agent Now / cron enqueue, **(B)** github_radar → write_post, **(C)** UI vendor labels + design unify?

---

## Artifacts to trust

| Artifact | Use |
|---|---|
| This `HANDOFF.md` | Session bridge — full review + Port pivot |
| `docs/specs/*` | Product truth |
| `docs/reference/*` | Intended patterns (may oversell vs code) |
| `tickets.md` | Stale checklist — verify against code |
| Live Port docs / June 2026 notes | Newest Port features (prefer over Ocean-centric design prose) |

---

*End of handoff. Next agent: read this fully, then specs, then ask which P0 slice to implement — default recommendation is Port Workflows/actions that actually run agents (P0a), because the user is Port and the showcase currently fails the Port collapse test.*
