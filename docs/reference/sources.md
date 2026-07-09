# Sources — Verification Trail

Every doc above traces to these. Verify before relying on specifics (star counts, feature availability).

## Port.io (official)

- Ocean Framework repo: <https://github.com/port-labs/ocean> (Python SDK, scaffold via `ocean new`, run via `ocean sail`)
- Ocean Framework docs: <https://docs.getport.io/build-your-software-catalog/custom-integration/ocean-framework/>
- Blueprints docs: <https://docs.getport.io/build-your-software-catalog/customize-integrations/configure-data-model/setup-blueprint/>
- Scorecards overview: <https://docs.getport.io/scorecards/overview>
- Self-Service Actions: <https://docs.getport.io/create-self-service-experiences/>
- Terraform provider: <https://github.com/port-labs/terraform-provider-port-labs>
- GitHub Action: <https://github.com/port-labs/port-github-action>
- Port Agent: <https://github.com/port-labs/port-agent>
- Experimental SDKs (JS/Py/CLI): <https://github.com/port-experimental> — evaluate before prod

## MongoDB Atlas (official)

- Vector Search overview: <https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-overview/>
- Automated Embedding: <https://www.mongodb.com/docs/vector-search/crud-embeddings/automated-embedding/overview/>
- Native Reranking (`$rerank`) announcement: <https://www.mongodb.com/products/updates/now-in-public-preview-native-reranking-rerank-on-atlas/>
- Rerank models (rerank-2.5): <https://www.mongodb.com/company/blog/product-release-announcements/rerank-2-5-and-rerank-2-5-lite-instruction-following-rerankers>
- Time Series collections: <https://www.mongodb.com/docs/manual/core/timeseries-collections/>
- Time Series best practices (community): <https://medium.com/@94giovanni/best-practices-for-mongodb-timeseries-collection-3fa9d29405ab>
- LangGraph long-term memory store: <https://www.mongodb.com/docs/atlas/ai-integrations/langgraph-js/long-term-memory-store/>
- LangGraph with MongoDB (blog): <https://dev.to/mongodb/langgraph-with-mongodb-building-conversational-long-term-memory-for-intelligent-ai-agents-2pcn>
- AI search for agents (auto-embedding announcement): <https://www.mongodb.com/company/blog/product-release-announcements/ai-search-for-agents-announcing-automated-embedding-atlas>

## MongoDB skills (local, MongoDB-maintained)

- `mongodb-schema-design` — schema patterns, anti-patterns, embed-vs-reference, time-series, validation
- `mongodb-search-and-ai` — Atlas Search, Vector Search, Hybrid Search, `$rerank`, index design
- `mongodb-connection` — connection pool config per runtime (serverless, long-running, analytical)
- `mongodb-query-optimizer` — for later: optimize slow feed/signal queries
- `mongodb-natural-language-querying` — for later: NL → aggregation for the admin portal

## Inspiration repos (product shape only — NOT our stack)

- Project AIA (agent-curated HN+GitHub feed): <https://github.com/razel369/aia>
- Firecrawl (web→markdown scraping): <https://github.com/mendableai/firecrawl>
- Browser-use (autonomous browser nav): <https://github.com/browser-use/browser-use>
- Skyvern (vision-based scraping): <https://github.com/Skyvern-AI/skyvern>
- CrewAI (multi-agent orchestration): <https://github.com/joaomdmoura/crewAI>

## Caveats

- Some star counts from the initial trend research (e.g. "OpenClaw 347k") came from a single synthesized source and must be verified against GitHub directly before publishing any HypeRadar content about them.
- Experimental Port SDKs (`port-experimental`) and MongoDB preview features (`$rerank`, auto-embedding) — confirm current GA/preview status before building production logic on them.
- Port docs base URL: observed both `docs.port.io` and `docs.getport.io` — use whichever the official site currently redirects to.
