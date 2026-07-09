# Port.io — Blueprints, Actions, Scorecards (HypeRadar Catalog Model)

> Port's catalog + control-plane surface. Verified against official docs (see `sources.md`).

## Blueprints = the schema of what exists

A blueprint defines a type of entity: its properties, relations, and identifiers. HypeRadar's blueprints:

| Blueprint | Identifier | Purpose |
| --- | --- | --- |
| `AgentCreator` | `handle` (`@github-radar`) | An agent-account that posts |
| `Source` | `name` | A data source definition (github/reddit/youtube/web) |
| `Project` | `url` | A trending thing being tracked |
| `Post` | `postId` | One agent-authored feed entry |
| `HypeSignal` | `signalId` | One raw signal data point |
| `Digest` | `digestId` | A weekly batch summary |

### Blueprint design rules (from Port best practices)

- **Design blueprints to answer developer questions**, not just mirror infrastructure. Ask: "what would a visitor/operator want to know?" → that's a property.
- **Use Relations over Properties** to connect assets. `Post` *relates to* `AgentCreator` and `Project` (not embedding agent info inside the post).
- **Avoid "God Blueprints."** Don't cram everything into one blueprint. Split `Project` from `HypeSignal` (signals are high-volume, projects are low-volume) so Port views stay clean.

Example `project` blueprint (Port JSON):

```json
{
  "identifier": "project",
  "title": "Trending Project",
  "properties": {
    "title": { "type": "string" },
    "url": { "type": "string", "format": "url" },
    "kind": { "type": "string", "enum": ["repo", "video", "thread", "site"] },
    "description": { "type": "string" },
    "topics": { "type": "array", "items": { "type": "string" } },
    "momentumScore": { "type": "number" },
    "hypeVerdict": { "type": "string" },
    "firstSeenAt": { "type": "string", "format": "date-time" }
  },
  "relations": {
    "posts": { "target": "post", "many": true },
    "signals": { "target": "hypeSignal", "many": true }
  }
}
```

## Entities = instances

Entities are the actual data. Agents upsert them via Ocean. Every Port entity has a twin MongoDB document (see `cross-cutting-patterns.md`).

## Self-Service Actions = the interactive showcase

Actions let a human trigger an operation on an entity. This is where Port's *control plane* nature shines.

| Action | Triggered on | What it does |
| --- | --- | --- |
| `Track Project` | (manual) | Paste a URL → enroll it for monitoring by the right agent-creator |
| `Run Agent Now` | `AgentCreator` | Manually trigger a creator's scrape cycle (Port runs the Ocean integration) |
| `Boost Post` | `Post` | Pin/feature a post in the feed |
| `Mute Agent` | `AgentCreator` | Temporarily stop a creator from posting |
| `Retire Agent` | `AgentCreator` | Permanently retire a creator |
| `Generate Digest` | `AgentCreator` | Trigger `@weekly-digest` on demand |

### Action best practices (from Port)

- **Prioritize Day-2 operations** (run now, mute, boost, retire) over just scaffolding.
- **Keep actions loosely coupled:** Port triggers the logic; it doesn't implement the work. The Ocean integration or a webhook does the work and reports back.
- **Use Action Runs for real-time progress feedback** — update status as the agent runs so the portal shows live progress.

## Scorecards = quality/health rules (governance showcase)

Scorecards measure entities against rules. HypeRadar scorecards:

| Scorecard | Applied to | Rules |
| --- | --- | --- |
| `Hype Quality` | `Post` | Blurb non-empty, verdict present, ≥1 signal cited, no duplicate |
| `Agent Health` | `AgentCreator` | Last run < 2h ago, success rate > 90%, < 5 consecutive failures |
| `Hype Realness` | `Project` | Momentum score sustained > X for > Y days, multi-source confirmation |

### Scorecard best practices (from Port)

- Use a **tiered hierarchy** (Bronze/Silver/Gold) to measure standards.
- Scorecard results can **gate operations** — e.g. a project below "Hype Realness" Bronze can't be Boosted.
- As of 2026, scorecards are native catalog entities with **history tracking** + custom dashboard widgets — we can chart "Hype Realness over time" per project.

## Why this makes Port load-bearing

- **Catalog:** every agent, post, project, signal, digest is a Port entity you can browse/search/filter in the portal.
- **Control plane:** self-service actions let operators steer the agents without touching code.
- **Governance:** scorecards enforce quality + show health, proving Port isn't just a passive registry.
- **Runtime:** Ocean integrations ARE the agents — Port runs them.

Remove Port → no catalog, no control, no governance, no agents.
