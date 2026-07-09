# MongoDB — Connection Configuration for HypeRadar

> Grounded in the `mongodb-connection` skill (MongoDB-maintained). **Context before configuration** — never copy params blindly.

## Our runtimes (the context)

| Runtime | Language/Driver | Workload | Concurrency |
| --- | --- | --- | --- |
| Next.js on Cloudflare (Workers) | Node.js driver | Read-heavy (feed, project pages, reactions) | Bursty, many instances |
| Ocean agent-creators (Python) | Motor (async PyMongo) | Write-heavy (signals, posts, upserts) | Low concurrency per agent, scheduled |
| Agent brain (LangGraph, Python) | Motor | Read + write (vector search, checkpoint, store) | Low concurrency, long-ish ops |

## Next.js on Cloudflare Workers — serverless pattern

**Critical:** initialize the MongoClient **outside** the request handler so warm invocations reuse the connection.

```ts
// lib/mongo.ts — module scope, reuses across warm invocations
import { MongoClient } from "mongodb";

const uri = process.env.MONGODB_URI!;
const client = new MongoClient(uri, {
  maxPoolSize: 5,        // each Worker instance has its own pool
  minPoolSize: 0,        // no idle connections between requests
  maxIdleTimeMS: 15000,  // release unused connections fast (serverless)
  connectTimeoutMS: 10000,
  socketTimeoutMS: 30000,
});

export const db = client.db("hyperadar");
```

### Rationale (per skill)

- `maxPoolSize: 5` — each Worker instance has its own pool; many instances × small pool avoids exhausting server connections. Formula from skill: `instances × (maxPoolSize + 2) × replica members`.
- `minPoolSize: 0` — don't hold connections in serverless (cold starts are acceptable).
- `maxIdleTimeMS: 15s` — serverless functions should release connections quickly.
- `connectTimeoutMS > 0` — must exceed network latency to the cluster.

> **Watch for:** Cloudflare Workers have connection limits. If we hit `MongoWaitQueueTimeoutError`, check `connections.current` on the cluster before increasing pool size. If the server is at capacity, optimize queries instead.

## Ocean agent-creators (Python / Motor) — long-running process pattern

Each agent-creator is a containerized long-running process triggered by Port. Low concurrency, scheduled.

```python
from motor.motor_asyncio import AsyncIOMotorClient

client = AsyncIOMotorClient(
    MONGODB_URI,
    maxPoolSize=10,       # low concurrency per agent
    minPoolSize=2,        # a couple pre-warmed
    maxIdleTimeMS=300000, # 5 min — long-running, keep connections
    connectTimeoutMS=10000,
    socketTimeoutMS=30000,
)
db = client.hyperadar
```

### Rationale

- `maxPoolSize: 10` — agents do sequential-ish work, low concurrency. 10 + headroom.
- `minPoolSize: 2` — pre-warm a couple to avoid cold connection on first upsert.
- `maxIdleTimeMS: 5min` — long-running process benefits from persistent connections.

## Agent brain (LangGraph) — analytical-ish, long operations

Vector search + `$rerank` + checkpoint can be slower ops.

```python
client = AsyncIOMotorClient(
    MONGODB_URI,
    maxPoolSize=10,
    socketTimeoutMS=60000,  # vector search / rerank can take longer
    maxIdleTimeMS=600000,
)
```

`socketTimeoutMS` set higher to avoid killing legitimate slow search ops. From the skill: for analytical-ish workloads, set `socketTimeoutMS` to 2-3× the slowest operation.

## Server-side capacity planning

Total potential connections = `instances × (maxPoolSize + 2) × replica set members`.

Example: 20 Worker instances × (5 + 2) × 3 members = 420 connections. Each connection ~1MB server RAM → ~420MB. Monitor `connections.current`; set `maxIncomingConnections` on self-managed clusters slightly above the expected max.

For Atlas, the tier (M10+) determines connection limits — size the tier to our peak instance count.

## Connection pitfalls

- **Don't create a new client per request** (serverless anti-pattern) — reuse at module scope.
- **Don't manually close connections** unless shutting down — let the pool manage them.
- **Don't set `maxPoolSize` without knowing concurrency** — arbitrary high values waste server RAM.
- **Do use timeouts** — `connectTimeoutMS` + `socketTimeoutMS` prevent hanging.
