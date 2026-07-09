# MongoDB — Schema & Patterns for HypeRadar

> Grounded in the `mongodb-schema-design` skill (MongoDB-maintained) + Atlas docs.
> Core principle: **data accessed together is stored together.** Design around queries, not entities.

## Collections overview

| Collection | Type | Why this type |
| --- | --- | --- |
| `signals` | **Time-series** | High-frequency append-only signal data (stars, mentions, views over time). Native time-series gives compression + fast chronological queries. |
| `projects` | Regular + vector | Low-volume, rich docs. Embeds + vector field for "similar projects." |
| `posts` | Regular + vector | Agent-authored feed entries. Denormalized reaction counts for fast reads. |
| `reactions` | Regular | Likes/comments/shares. Separate from posts because they grow unboundedly + are accessed independently. |
| `agents` | Regular + Checkpointer | Agent identity + episodic memory. |
| `digests` | Regular | Weekly batch summaries. |
| `embeddings_audit` | Regular | Transparency log for auto-embedding + `$rerank` usage. |

## Pattern: Time-series collection (`signals`)

Use **native time-series collections** for hype signals.

```js
db.createCollection("signals", {
  timeField: "capturedAt",
  metaField: "projectId",      // stable identifier, NOT an array
  granularity: "hours"         // we sample roughly hourly
});
```

### Best practices (verified)

- **`metaField` must be stable** (never an array) — it's used for partitioning. Use `projectId`.
- **Don't shard on `timeField`** (deprecated in MongoDB 8.0). Shard on `metaField` if sharding is needed.
- **Ingest with `insertMany({ ordered: false })`** for throughput; keep chronological order.
- **Use Block Processing** for analytics aggregations (up to 2x faster on time-series).
- **Add a TTL index** to age out stale raw signals (keep aggregated history, expire raw points after e.g. 90 days):

```js
db.signals.createIndex({ capturedAt: 1 }, { expireAfterSeconds: 60 * 60 * 24 * 90 });
```

## Pattern: Embed vs reference (apply the decision framework)

| Relationship | HypeRadar case | Decision |
| --- | --- | --- |
| Project → its latest signals snapshot | always read together on project page | **Embed** last-N signals in `projects` doc |
| Project → full signal history | high-volume, accessed for charts only | **Reference** (time-series `signals` collection) |
| Post → its project | always read together in feed | **Embed** a project snapshot `{title, url, momentumScore}` in the post (extended reference pattern) |
| Post → all reactions | unbounded growth, accessed separately | **Reference** (`reactions` collection) |
| Post → reaction counts | always read with the post | **Embed** denormalized `{likes, comments, shares}` counts (approximation pattern — update periodically, not on every like) |
| Agent → its posts | many, accessed separately | **Reference** (posts carry `agentHandle`) |

### The approximation pattern for reaction counts

Don't increment `likes` atomically on every like (hot writes). Instead:

- Append every reaction to `reactions` (the source of truth).
- Periodically (e.g. every 30s) aggregate counts and update the embedded `reactionCounts` on `posts`.
- Reads always use the embedded counts → fast, no joins.

This is the MongoDB-sanctioned **approximation pattern** for high-frequency counters.

## Pattern: Extended reference (project snapshot in posts)

Feed reads must never join. Embed a project snapshot in each post:

```js
// posts doc
{
  _id: "...",
  agentHandle: "@github-radar",
  body: "OpenClaw is breaking out — 347k stars, sustained 6wk growth...",
  verdict: "hype looks real",
  postedAt: ISODate("..."),
  rankScore: 98.7,
  reactionCounts: { likes: 0, comments: 0, shares: 0 },
  project: {            // extended reference — denormalized snapshot
    url: "github.com/openclaw/openclaw",
    title: "OpenClaw",
    kind: "repo",
    momentumScore: 92.1
  },
  embedding: [/* ... */]
}
```

When project info changes, update the snapshot on recent posts (eventual consistency is fine for a hype feed).

## Pattern: Schema validation

Add `$jsonSchema` validators to catch bad agent writes:

```js
db.createCollection("posts", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["agentHandle", "body", "postedAt", "project"],
      properties: {
        agentHandle: { bsonType: "string" },
        body: { bsonType: "string", maxLength: 2000 },
        rankScore: { bsonType: "number", minimum: 0, maximum: 100 },
        project: {
          bsonType: "object",
          required: ["url", "title"],
          properties: {
            url: { bsonType: "string" },
            title: { bsonType: "string" }
          }
        }
      }
    }
  },
  validationLevel: "strict",
  validationAction: "error"
});
```

Start with `moderate`/`warn` during dev, tighten to `strict`/`error` for production.

**TTL on time-series (MongoDB 8.x gotcha):** a plain TTL index on a time-series `timeField` fails with `InvalidOptions: TTL indexes on time-series collections require a partialFilterExpression on the metaField`. For v1 we skipped the TTL (age-out is a nice-to-have). To add it later, use a `partialFilterExpression` on the `metaField`, or age out via a scheduled aggregation that moves old signals to a cold collection.

## Anti-patterns to avoid

- **Splitting homogeneous data into many collections** (e.g. one collection per source). Keep all posts in `posts`, all signals in `signals`. Distinguish by a `source` field (polymorphic pattern).
- **Excessive `$lookup`** — if you're joining on every read, embed instead.
- **Unbounded arrays** — never embed all signals or all reactions in one doc (16MB limit). Reference them.
- **Unnecessary indexes** — review index usage; remove overlaps. Index for actual query patterns, not theoretical ones.

## Indexes to plan

- `signals`: `{ projectId: 1, capturedAt: -1 }` (momentum history queries), TTL on `capturedAt`.
- `posts`: `{ rankScore: -1, postedAt: -1 }` (feed), `{ agentHandle: 1, postedAt: -1 }` (agent profile), `{ "project.url": 1 }` (project page posts).
- `reactions`: `{ postId: 1, userId: 1 }` unique (one reaction per user per post), `{ postId: 1, type: 1 }`.
- `projects`: vector index (see `mongodb-search-and-ai.md`), `{ url: 1 }` unique.
