# Port.io Ocean Framework — Best Practices for HypeRadar Agent-Creators

> Source of truth for building each agent-creator as a Port Ocean integration.
> Verified against `github.com/port-labs/ocean` + official docs (see `sources.md`).

## What Ocean is

Ocean is Port's open-source **Python SDK** (Python 3.11+) for building custom integrations. An integration = a Python service that Port schedules, runs, and lifecycles. It reads from a third-party source and upserts entities into Port's catalog.

**This is why Port is load-bearing in HypeRadar:** every agent-creator (`@github-radar`, `@reddit-pulse`, `@youtube-trends`, `@hidden-gems`, `@weekly-digest`) IS an Ocean integration. Remove Port → no agents run → no feed.

## Folder structure (per integration)

Each agent-creator is one folder under `integrations/`:

```
hyperadar-agents/
└── integrations/
    ├── github_radar/
    │   ├── main.py          # core logic
    │   ├── pyproject.toml   # deps
    │   ├── Dockerfile       # containerized deploy
    │   └── config.yaml      # framework + integration config
    ├── reddit_pulse/
    ├── youtube_trends/
    ├── hidden_gems/
    └── weekly_digest/
```

## Scaffold a new agent-creator

```bash
pip install "port-ocean[cli]"   # or: poetry add "port-ocean[cli]"
ocean new ./integrations/github_radar   # scaffold (follow prompts)
cd integrations/github_radar && make install
. .venv/bin/activate
ocean sail ./integrations/github_radar  # run locally
```

## Core patterns (verified)

### 1. Use `async for` generators for resync logic

Streaming prevents memory exhaustion when a source returns many items (e.g. GitHub trending across 50 languages). Yield entities one at a time:

```python
async def resync_resource(kind, client):
    async for repo in client.iter_trending():
        yield repo  # Port upserts one at a time
```

### 2. Keep data transformation in JQ mappings (`port-app-config.yml`)

Don't hardcode transforms in Python. Put them in JQ mappings so they're customizable without redeploying the integration:

```yaml
# port-app-config.yml
port:
  entities:
    - blueprint: project
      mappings:
        # JQ transforms raw source data → Port entity properties
        body: |-
          {
            "identifier": .id,
            "title": .name,
            "url": .html_url,
            "topics": .topics,
            "momentumScore": .momentum_score
          }
```

### 3. Webhooks for real-time updates

Inherit from `AbstractWebhookProcessor` for live event ingestion (e.g. Reddit webhooks, GitHub push events) instead of pure polling.

### 4. Encrypted action inputs

When self-service actions carry secrets (API tokens for a source), use Ocean's encrypted action inputs — never log or store them in plaintext.

## How an agent-creator run works (HypeRadar loop)

```
1. Port schedules the integration (cron, e.g. every 1h for github_radar)
2. main.py scrapes the source (GitHub trending / Reddit / YouTube)
3. For each candidate:
   a. Query MongoDB for momentum history (see mongodb-agent-memory.md)
   b. Score "real trend vs noise" using $rerank over prior signals
   c. MongoDB Checkpointer logs the agent's reasoning episode
4. Upsert raw signals → MongoDB time-series `signals` collection
5. Upsert project → MongoDB `projects` (auto-embedded) + Port `project` entity
6. Create a Post entity in Port + a post doc in MongoDB `posts`
7. Port tracks run state (lastRunAt, runCount, status) on the AgentCreator entity
```

## Deployment

Ocean integrations deploy as containers (Docker/K8s). Config via environment variables. For HypeRadar we'll deploy each agent-creator on **Cloudflare Containers** (GA Apr 2026, Active-CPU billing — pay only for CPU during the once-daily run). Port orchestrates/schedules the containers. Fallback: Port-hosted SaaS runtime. See `docs/specs/2026-07-09-hyperadar-design.md` Section 6.

## Pitfalls

- **Don't block on sync I/O** — Ocean is async. Use `httpx`/`aiohttp`, not `requests`.
- **Don't upsert huge batches in one call** — stream with `async for`.
- **Don't put transforms in Python when JQ works** — breaks customizability.
- **Don't forget rate limits** — GitHub (5k req/h authenticated), Reddit, YouTube all have limits. Ocean has rate-limiting patterns in its skill references (`references/rate-limiting-patterns.md`).
