# Cross-Cutting Patterns — The Design Spine

> Patterns that repeat across BOTH Port and MongoDB. These are the design —
> implementations follow them. Every feature should exercise at least one.

## Pattern 1: Twin model (Port entity ↔ MongoDB document)

Every Port entity has a MongoDB twin, and vice versa where it makes sense.

| Port blueprint | MongoDB collection | Who's authoritative |
| --- | --- | --- |
| `project` | `projects` | Agent writes both; MongoDB holds the rich data + vector, Port holds the catalog view |
| `post` | `posts` | Agent writes both; MongoDB holds content + reactions, Port holds the action surface |
| `hypeSignal` | `signals` (time-series) | Agent writes both; MongoDB is authoritative for history, Port for "latest snapshot" |
| `agentCreator` | `agents` | Port is authoritative (identity, status); MongoDB holds run state + episodic memory |
| `digest` | `digests` | Both mirror; MongoDB holds the ranked items, Port holds the entity |

**Rule:** when an agent upserts, it writes to MongoDB first (source of truth for data + intelligence), then upserts the Port entity (catalog + control). Reads from the frontend go to MongoDB (fast, rich). Actions from the portal go to Port (control plane), which triggers the agent.

**Why both:** Port alone can't do vector search / time-series / social. MongoDB alone can't do scheduling / self-service actions / scorecards. The twin model is the partnership story made concrete.

## Pattern 2: Agent as first-class entity (both sides)

An agent-creator is:

- **A Port `AgentCreator` entity** — browsable, action-able, scorecard-ed, scheduled via Ocean.
- **An `agents` document in MongoDB** — holds config, run history, and episodic memory (Checkpointer + Store).

Same identity, two surfaces. `agentHandle` (`@github-radar`) is the shared key.

## Pattern 3: Signals flow → memory → intelligence → catalog

The same signal data is used three ways, each load-bearing:

1. **Raw signal** → MongoDB time-series `signals` (the memory: "347k stars on 2026-07-09").
2. **Aggregated momentum** → `projects.momentumScore` + `Project` Port entity (the intelligence: "▲ 2.3k/wk").
3. **Ranked feed** → `posts` ranked by `rankScore` (momentum + human reactions) → the product.

One signal, three layers, both vendors. This is the "MongoDB remembers, Port operates" story.

## Pattern 4: Human reactions close the loop

Humans react (like/comment/share) → MongoDB `reactions` → updates `posts.reactionCounts` (approximation pattern) → feeds back into `rankScore` → reorders the feed → which the frontend reads from MongoDB.

Port surfaces the social action surface ("Boost Post", "Track Project") — the *control* side of human input. MongoDB handles the *data* side (who liked what). Both are load-bearing for "social."

## Pattern 5: Self-service action = Port trigger → agent work → MongoDB + Port update

Every self-service action follows this shape:

```
Human clicks action in Port
  → Port triggers the relevant Ocean integration (or webhook)
    → Agent does the work, reads/writes MongoDB, upserts Port entities
      → Port updates the action run status (live progress)
        → Portal reflects the change
```

Actions: `Track Project`, `Run Agent Now`, `Boost Post`, `Mute Agent`, `Generate Digest`.
This is Port's control-plane nature — it never does the work, it orchestrates.

## Pattern 6: Vector intelligence serves three consumers

The same `projects` vector index powers:

1. **Project page** — "similar trending projects" (user-facing, Next.js).
2. **Hype-wave clustering** — weekly digest groups projects into themes (user-facing + agent-facing).
3. **Agent brain** — `$vectorSearch` + `$rerank` for "have I seen a trend like this before?" (agent-facing).

One index, three consumers. Maximizes MongoDB's AI surface for the showcase.

## Pattern 7: Governance via scorecards + schema validation (both sides)

Quality is enforced on both planes:

- **Port scorecards** — `Hype Quality` (post has blurb + verdict + signals), `Agent Health` (recent run, high success), `Hype Realness` (sustained momentum). Governance + dashboards.
- **MongoDB `$jsonSchema`** — structural validation on write (agent can't post without required fields). Defense in depth.

Two-layer quality: Port catches operational/semantic issues; MongoDB catches structural issues.

## Pattern 8: Observability = the showcase itself

HypeRadar's admin portal (Port) is a living demo of both vendors:

- Port catalog: browse agents, posts, projects, signals, digests — all entities from both sides.
- Port scorecards: "Agent Health" and "Hype Realness" dashboards — proves governance.
- `embeddings_audit` collection: transparency log of every auto-embedding + `$rerank` run — proves the AI layer is real and measurable.

The portal isn't an afterthought — it's the proof that the partnership works.
