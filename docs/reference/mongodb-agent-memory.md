# MongoDB — Agent Memory (Checkpointer + Long-Term Store)

> The agent-creators' "brain." Grounded in MongoDB LangGraph/LangChain docs (see `sources.md`).
> This makes MongoDB load-bearing for the agents' *reasoning*, not just storage.

## Two memory layers (distinct — don't conflate)

| Layer | Tool | Purpose | HypeRadar use |
| --- | --- | --- | --- |
| **Short-term (state)** | `MongoDBSaver` (Checkpointer) | Persist a single agent run's state — survive restarts, enable resume | An agent's current scrape cycle: candidates seen, decisions made mid-run |
| **Long-term (episodic)** | `MongoDBStore` | Cross-session learning — store distilled episodes of good decisions | "Last time OpenClaw spiked, these signals preceded it" — few-shot examples for future trend detection |

## Short-term: MongoDBSaver (Checkpointer)

Powers resumable agent runs. If an agent crashes mid-scrape, it resumes from the last checkpoint.

```python
from langgraph.checkpoint.mongodb import MongoDBSaver

checkpointer = MongoDBSaver.from_conn_string(MONGODB_URI)
checkpointer.setup()  # creates required collections/indexes — call once

graph = build_agent_graph().compile(checkpointer=checkpointer)
```

### Best practices (verified)

- **Always call `.setup()`** once to create checkpoint collections + indexes.
- **Use a TTL index** to expire old conversation/run states (don't keep forever):

```js
db.checkpoints.createIndex({ "ts": 1 }, { expireAfterSeconds: 60 * 60 * 24 * 7 }); // 7 days
```

- Each agent-creator uses its own `thread_id` (e.g. `github-radar:run:2026-07-09T12:00`) so runs don't collide.

## Long-term: MongoDBStore (episodic memory)

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

## The agent brain loop (putting it together)

```
For each candidate project from the source:
  1. Retrieve momentum history from time-series `signals`
  2. Retrieve similar past episodes from MongoDBStore (long-term memory)
  3. Compose a scoring prompt: candidate + momentum + few-shot episodes
  4. LLM scores "real hype vs noise" + writes a blurb/verdict
  5. Checkpointer logs the decision (short-term, for this run)
  6. If "real hype":
     a. Upsert signals → MongoDB time-series
     b. Upsert project → MongoDB (auto-embedded)
     c. Upsert post → MongoDB + Port entity
     d. Store a distilled episode → MongoDBStore (long-term, for future runs)
```

## Why this makes MongoDB load-bearing (the brain argument)

- **Short-term:** without the checkpointer, agent runs can't resume → fragile, no observability of reasoning.
- **Long-term:** without episodic memory, every run starts from scratch → no learning, no "the agent got better at spotting hype over time" story.
- Remove MongoDB's memory layer → the agents are stateless, memoryless scrapers. The whole "agent brain" story collapses.
