# MongoDB — Agent Memory Architecture

> Current behavior and target architecture, grounded in MongoDB
> LangGraph/LangChain docs (see `sources.md`).

## Current implementation status

`MongoDBSaver` currently checkpoints each run. The custom `episodes` collection
stores embeddings and supports Atlas Vector Search retrieval. The shared write
path retrieves similar episodes and records them as transparent context on the
published post, but retrieval happens after the LLM has selected its verdict.
Therefore HypeRadar does **not** currently claim that episodes improve verdicts
or that agents learn across runs. The pre-verdict loop below is the target design.

## Two memory layers (distinct — don't conflate)

| Layer | Tool | Purpose | HypeRadar use |
| --- | --- | --- | --- |
| **Short-term (current)** | `MongoDBSaver` (Checkpointer) | Persist one run's execution trace | Inspect the current scrape cycle after execution |
| **Long-term (partial)** | Custom `episodes` collection + Atlas Vector Search | Store and retrieve distilled episodes | Attach similar historical evidence to a published post |
| **Learning loop (target)** | Pre-verdict episodic retrieval | Supply recalled lessons to agent reasoning | Influence a future verdict with prior confirmed outcomes |

## Current short-term state: MongoDBSaver

Persists a durable trace for each run. Every invocation currently generates a
fresh timestamped thread ID, so automatic resume into a failed prior invocation
is not implemented and must not be claimed in the demo.

```python
from langgraph.checkpoint.mongodb import MongoDBSaver

checkpointer = MongoDBSaver.from_conn_string(MONGODB_URI)
checkpointer.setup()  # creates required collections/indexes — call once

graph = build_agent_graph().compile(checkpointer=checkpointer)
```

### Target operational hardening

- **Always call `.setup()`** once to create checkpoint collections + indexes.
- **Use a TTL index** to expire old conversation/run states (don't keep forever):

```js
db.checkpoints.createIndex({ "ts": 1 }, { expireAfterSeconds: 60 * 60 * 24 * 7 }); // 7 days
```

- Each agent-creator uses its own `thread_id` (e.g. `github-radar:run:2026-07-09T12:00`) so runs don't collide.

## Target: long-term MongoDBStore episodic memory

Cross-run learning. After a successful trend detection (a project the agent flagged *actually* blew up), store a distilled episode — not raw logs.

```python
from langgraph.store.mongodb import MongoDBStore

store = MongoDBStore.from_conn_string(MONGODB_URI)
store.setup()  # creates indexable collections

# Store a distilled episode (NOT raw logs)
store.put(
    namespace=("agent", "github-radar", "episodes"),
    key="openclaw-breakout-2026-05",
    value={
        "project": "openclaw",
        "signals_preceding": {"star_velocity_wk": 34000, "reddit_mentions_wk": 210},
        "verdict": "real hype",
        "outcome": "confirmed — hit 347k stars 4 weeks later",
        "lesson": "high star_velocity + rising reddit mentions + novel category = real hype"
    }
)
```

### Retrieve past episodes as few-shot examples (Vector Search on the store)

The agent, when scoring a new candidate, retrieves similar past episodes:

```python
episodes = store.search(
    namespace=("agent", "github-radar", "episodes"),
    query="AI agent framework with rapid star growth and reddit buzz",
    limit=3
)
# feed these as few-shot context into the agent's scoring prompt
```

This is **agentic RAG over the agent's own history** — the headline demo of MongoDB as agent brain.

## Target agent brain loop

```
For each candidate project from the source:
  1. Retrieve momentum history from time-series `signals`
  2. Retrieve similar past episodes from MongoDBStore (long-term memory)
  3. Compose a scoring prompt: candidate + momentum + few-shot episodes
  4. LLM scores "real hype vs noise" + writes a blurb/verdict
  5. Checkpointer logs the decision (short-term, for this run)
  6. If "real hype":
     a. Upsert signals → MongoDB time-series
     b. Generate a local embedding and upsert the project → MongoDB
     c. Upsert post → MongoDB + Port entity
     d. Store a distilled episode → MongoDBStore (long-term, for future runs)
```

## Target value of the completed memory loop

- **Current:** MongoDB is load-bearing for source observations, projects, posts,
  social reactions, vector retrieval, and durable checkpoint traces.
- **Not current:** automatic crash resume and episode-informed verdict improvement.
- **Target:** moving retrieval before verdict selection would complete the
  cross-run learning loop described above.
