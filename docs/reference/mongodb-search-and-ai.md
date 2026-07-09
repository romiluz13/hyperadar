# MongoDB — Search & AI (Vector Search, Auto-Embedding, `$rerank`, Hype-Wave Clustering)

> Grounded in the `mongodb-search-and-ai` skill + Atlas AI docs (see `sources.md`).
> This is the **intelligence layer** that makes MongoDB load-bearing for HypeRadar.

## Search type decision (from the skill)

| Need | Search type | Where used |
| --- | --- | --- |
| "Find trending projects similar to X" | **Vector Search** | Project page "similar projects" section |
| "Search the feed for posts about local-first agents" | **Vector Search** (on `posts`) | Feed search bar |
| "Cluster this week's projects into hype waves" | **Vector Search** + clustering | Weekly digest, radar dashboard |
| Agent: "have I seen a project like this before?" | **Vector Search** + `$rerank` | Agent trend-detection brain |
| Autocomplete on project titles | **Atlas Search (lexical)** | Search bar typeahead |

Never use `$regex` or `$text` for these — use Atlas Search / Vector Search (skill anti-pattern).

## Auto-embedding (eliminates external embedding pipelines)

MongoDB Atlas **Automated Embedding** (powered by Voyage AI) generates embeddings on document fields automatically — no external embedding step in the agent code.

Enable on `projects` (embed `description` + `topics`):

```json
// Atlas UI / API: automated embedding definition
{
  "name": "projects_embedding_index",
  "type": "vector",
  "fields": [{
    "name": "embedding",
    "type": "vector",
    "path": "description_topics",
    "similarity": "cosine",
    "quantization": "int8"   // reduces storage ~75%
  }]
}
```

The agent just upserts the project doc with `description` + `topics` fields; Atlas auto-embeds. We log the run in `embeddings_audit` for the showcase.

## Vector Search index — `projects`

```json
{
  "fields": [{
    "type": "vector",
    "path": "embedding",
    "numDimensions": 1024,
    "similarity": "cosine"
  }, {
    "type": "filter",
    "path": "kind"
  }]
}
```

## Query: "similar trending projects" (project page)

```js
db.projects.aggregate([
  {
    $vectorSearch: {
      index: "projects_embedding_index",
      path: "embedding",
      queryVector: <projectEmbedding>,  // or use $vectorize with auto-embedding
      numCandidates: 50,
      limit: 10,
      filter: { kind: "repo" }          // optional prefilter
    }
  },
  { $project: { title: 1, url: 1, momentumScore: 1, _id: 0 } }
]);
```

## `$rerank` — native reranking aggregation stage

`$rerank` (public preview, 2026) uses cross-encoders inside the database to improve retrieval accuracy up to ~30% over plain vector search. **Two-stage retrieval:**

```js
db.projects.aggregate([
  // Stage 1: vector search for candidates
  {
    $vectorSearch: {
      index: "projects_embedding_index",
      path: "embedding",
      queryVector: <embed>,
      numCandidates: 50,
      limit: 20
    }
  },
  // Stage 2: native rerank with a cross-encoder
  {
    $rerank: {
      input: { $concat: ["$title", " ", "$description", " ", { $arrayToString: "$topics" }] },
      queryText: "local-first AI agent frameworks",
      model: "voyage-3-rerank",      // or rerank-2.5
      limit: 10
    }
  }
]);
```

**Where we use it:**

- Agent trend-detection: "is this candidate similar to previously-confirmed-trending projects?" → `$vectorSearch` candidates → `$rerank` against the agent's query.
- Feed search: better relevance than raw vector search.

## Hype-wave clustering (semantic grouping)

Cluster this week's trending projects into themes ("local-first agents", "MCP servers", "eval tooling"):

1. Query `projects` where `lastSeenAt` is within the last 7 days.
2. Run an in-app clustering step on embeddings (k-means or HDBSCAN) — small N, cheap.
3. Label each cluster by its centroid's nearest topics (or an LLM call summarizing cluster members).
4. Store clusters in the `digests` doc for the week → powers the weekly digest post + radar view.

This is a headline demo: **"MongoDB vector search discovers the hype waves of the week."**

## Lexical search (Atlas Search) for autocomplete

For typeahead on project titles (fast, typo-tolerant):

```json
// Atlas Search index on projects.title
{
  "mappings": {
    "dynamic": false,
    "fields": {
      "title": { "type": "autocomplete" }
    }
  }
}
```

```js
db.projects.aggregate([{
  $search: {
    index: "title_autocomplete",
    autocomplete: { query: "opencl", path: "title", fuzzy: { maxEdits: 1 } }
  }
}]);
```

## Production checklist (from skill + docs)

- Use **dedicated Search Nodes** for production (separates search load from OLTP).
- Use **int8/binary quantization** to cut vector storage ~75%.
- Always **inspect existing indexes before creating new ones** (avoid overlap).
- **Explain before execute** — show index JSON, get approval, then create.
- Version check: `$rankFusion` needs 8.0+, `$scoreFusion` needs 8.2+ (if we go hybrid).
