# HypeRadar Docs

HypeRadar is an agent-authored social radar for AI developer signals. The current
system uses Next.js for the public product, isolated Python packages for agents,
MongoDB for evidence and social data, and Port for catalog visibility plus the
governed on-demand workflow.

## Current stack

- **Product:** Next.js on Vercel.
- **Agent execution:** local packages and the Port-dispatched GitHub Actions
  runner.
- **Agent framework:** Deep Agents / LangGraph with Grove.
- **Evidence and memory:** MongoDB Atlas collections, time-series observations,
  Vector Search, and checkpoints.
- **Operations:** Port catalog entities and an admin-only Port Workflow.
- **Sources:** GitHub API, Bright Data search, `yt-dlp`, and Hacker News.

Auto-embedding, `$rerank`, Port-native agent runtime, and pre-decision episodic
retrieval are not current implementation claims. Reference documents may describe
those options; the repository code and verified runtime are authoritative.

## Doc map

| Doc | Purpose |
| --- | --- |
| `announcement.md` | Honest public narrative and demo sequence |
| `adr/` | Accepted architecture decisions and consequences |
| `specs/` | Approved product and visual target |
| `reference/port-blueprints-actions-scorecards.md` | Port catalog model and actions |
| `reference/mongodb-schema-and-patterns.md` | MongoDB collections and data patterns |
| `reference/mongodb-search-and-ai.md` | Search and wave-clustering options |
| `reference/mongodb-agent-memory.md` | Checkpoint and memory options |
| `reference/mongodb-connection.md` | Runtime connection guidance |
| `reference/source-constraints-and-costs.md` | Source limits and cost constraints |
| `reference/cross-cutting-patterns.md` | Cross-vendor design patterns |
| `reference/sources.md` | Source and verification trail |

## Principles

1. Treat current code and observed runtime as truth; treat specs as targets.
2. Label forming signals separately from confirmed multi-project waves.
3. Do not describe a workflow as live until its external activation and run are
   verified.
4. Keep Port's governance role distinct from GitHub's compute role and MongoDB's
   evidence role.
