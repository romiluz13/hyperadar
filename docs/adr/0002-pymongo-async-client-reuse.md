# ADR 0002: Use PyMongo Async with one client per event loop

Status: Accepted

## Context

The shared persistence helpers constructed a new Motor client for every database
operation. That defeated connection pooling, multiplied Atlas topology-monitoring
connections, and made small persistence tests take minutes. Motor was also
deprecated on May 14, 2026; MongoDB recommends the native PyMongo Async API.

HypeRadar agents run as short-lived, low-concurrency GitHub Actions jobs. Each
production run uses one event loop, while pytest can create multiple isolated
loops in one process. PyMongo Async clients must not be shared across event loops.

## Decision

Use PyMongo's `AsyncMongoClient` and cache one client per event loop. Explicitly
close and remove that client before its owning loop exits. The shared episodic-
memory functions use the same database client as projects, signals, and posts.
Keep a small maximum pool of 10 and no minimum idle pool for batch jobs.

Remove direct Motor dependencies from every agent package. Keep LangGraph's
checkpoint client lifecycle separate because `MongoDBSaver` owns that integration.

## Consequences

- Operations in one agent run reuse a real connection pool.
- Separate test event loops cannot accidentally share an async client.
- Runner `finally` blocks and the test fixture close pools and monitor tasks.
- Scheduled jobs do not pre-warm unused Atlas connections.
- PyMongo async network operations must be awaited; notably, `aggregate()` is
  awaited before consuming its cursor.
- Pool sizing should be revisited using Atlas connection metrics if agent
  concurrency materially increases.

## References

- [MongoDB: Migrate to PyMongo Async](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/migration/)
- [MongoDB: Connection pools](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/connection-options/connection-pools/)
