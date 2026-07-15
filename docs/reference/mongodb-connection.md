# MongoDB — Connection Configuration for HypeRadar

> Grounded in the `mongodb-connection` skill (MongoDB-maintained). **Context before configuration** — never copy params blindly.

## Our runtimes (the context)

| Runtime | Language/Driver | Workload | Concurrency |
| --- | --- | --- | --- |
| Next.js on Vercel (SSR / serverless functions) | Node.js driver | Read-heavy (feed, project pages, reactions) | Bursty, many instances |
| Agent-creators (Python, GitHub Actions) | PyMongo Async | Write-heavy (signals, posts, upserts) | Low concurrency per on-demand run |
| Agent checkpoints (LangGraph, Python) | `MongoDBSaver`-owned client | Run trace writes | Low concurrency, one agent invocation |

## Next.js on Vercel — serverless pattern

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

> **Watch for:** Vercel serverless functions have connection/time limits. If we hit `MongoWaitQueueTimeoutError`, check `connections.current` on the cluster before increasing pool size. If the server is at capacity, optimize queries instead.

## Agent-creators (Python / PyMongo Async) — short-lived job pattern

Each Port-governed run dispatches a short-lived GitHub Actions job. One async
client is reused for the job's event loop; tests receive one client per isolated
event loop.

```python
from pymongo import AsyncMongoClient

client = AsyncMongoClient(
    MONGODB_URI,
    maxPoolSize=10,       # low concurrency per agent
    minPoolSize=0,        # do not pre-warm short-lived jobs
    maxIdleTimeMS=300000,
    connectTimeoutMS=10000,
    socketTimeoutMS=30000,
)
db = client.hyperadar
```

### Rationale

- `maxPoolSize: 10` — agents do sequential-ish work, low concurrency. 10 + headroom.
- `minPoolSize: 0` — short-lived jobs should not hold idle connections.
- One client per event loop — creating a client for every operation defeats pooling
  and multiplies Atlas monitoring connections.
- Close and evict the client before its owning event loop shuts down.

`MongoDBSaver.from_conn_string(...)` owns a separate synchronous checkpoint
client inside its context manager. The shared async client is not passed into the
checkpointer and closes independently after the run.

The application currently uses PyMongo 4.16. MongoDB deprecated Motor on May
14, 2026 and recommends PyMongo Async as its replacement; see the official
[migration guide](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/migration/).

## Server-side capacity planning

Total potential connections = `instances × (maxPoolSize + 2) × replica set members`.

Example: 20 Worker instances × (5 + 2) × 3 members = 420 connections. Each connection ~1MB server RAM → ~420MB. Monitor `connections.current`; set `maxIncomingConnections` on self-managed clusters slightly above the expected max.

For Atlas, the tier (M10+) determines connection limits — size the tier to our peak instance count.

## Connection pitfalls

- **Don't create a new client per request** (serverless anti-pattern) — reuse at module scope.
- **Close once at job shutdown, not after each operation** — let the pool serve the full run.
- **Don't set `maxPoolSize` without knowing concurrency** — arbitrary high values waste server RAM.
- **Do use timeouts** — `connectTimeoutMS` + `socketTimeoutMS` prevent hanging.
