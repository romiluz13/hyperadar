# MongoDB Search and Vector Intelligence

> Current implementation first. Automated embedding, native `$rerank`, lexical
> feed search, and autocomplete are options, not current product claims.

## What runs today

| Surface | Current mechanism |
| --- | --- |
| Project dossier “related signals” | Atlas `$vectorSearch` on `projects.embedding` |
| Similar episode context | Atlas `$vectorSearch` on `episodes.embedding`, with a recent-episode fallback |
| Weekly hype waves | In-process cosine clustering over project embeddings, then Grove labels the clusters |
| Embedding generation | Local `all-MiniLM-L6-v2`, 384 dimensions |

The project dossier first restricts candidates to projects that have at least one
explicitly synchronized post. A project that has not converged with Port cannot leak back into
the public product through vector similarity.

## Current project vector index

`scripts/setup_mongodb.py` creates:

```python
SearchIndexModel(
    name="projects_vector_index",
    type="vectorSearch",
    definition={
        "fields": [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": 384,
                "similarity": "cosine",
            },
            {"type": "filter", "path": "url"},
        ]
    },
)
```

The page query uses the same `projects_vector_index` name, excludes the current
URL, and filters to URLs backed by synchronized posts.

## Current episode vector index

`episodes_vector_index` uses the same 384-dimensional embedding model and adds an
`agentHandle` filter. Retrieval currently happens inside the shared write path
after the agent chose its verdict. The retrieved episodes are evidence context,
not an input that improved that verdict.

## Current wave clustering

The weekly job selects projects from synchronized source-agent posts in the same
seven-day window, groups their stored embeddings by cosine similarity, derives
the recent source-agent handles, labels each cluster through Grove, and stores
the result in the week's MongoDB `digests` document.

This is semantic grouping over MongoDB-resident vectors, not evidence that the
projects moved in the same direction. It is not an Atlas `$vectorSearch`
clustering stage and does not use `$rerank`.

## Explicit future options

- Atlas automated embedding could replace local generation after an index and
  migration design is validated.
- Native `$rerank` could improve a two-stage retrieval path after its preview or
  GA API is validated against the deployed Atlas version.
- Atlas Search could add lexical feed search or title autocomplete.
- Moving episode retrieval before scoring could make historical outcomes an
  honest agent-reasoning input.

Until those changes exist in code and live proof, keep them out of the demo's
“what works now” narrative.
