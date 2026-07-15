# Port Runtime — Current Workflow and Ocean Option

> Runtime truth for HypeRadar. The current implementation uses a Port Workflow,
> Port's GitHub integration, GitHub Actions, and direct Port REST upserts.

## Current implementation

The five Python agent packages are not custom Ocean integrations. A governed run
crosses these boundaries:

```text
Port Workflow trigger
  → GitHub integration dispatches run-hyperadar-agent.yml
    → GitHub Actions runs one integrations/<agent>/main.py package
      → agent writes evidence to MongoDB
      → shared write path upserts agent, project, and post entities to Port
    → GitHub reports the final node result to Port
```

The Port GitHub installation happens to be named `github-ocean`; that is the
installed GitHub integration identifier, not proof that the Python agents use
the Ocean SDK.

## Why this split is intentional

- Port owns agent selection, authorization, dispatch, and the visible run trail.
- GitHub Actions supplies isolated compute and the committed `uv` environment.
- MongoDB stores rich evidence, vectors, social state, checkpoints, and posts.
- Port's REST catalog holds the operational twin of each published post.

Publication is fail-closed. A MongoDB post starts with
`portSyncStatus: "pending"`; the public app hides it until all required Port
upserts succeed. A retry repairs the same twin instead of creating another post.

## Repository surfaces

```text
.github/workflows/run-hyperadar-agent.yml  GitHub runner and Port reporting
scripts/setup_port_workflows.py            Idempotent Port Workflow provisioning
scripts/report_port_workflow_run.py        Final node status callback
integrations/_shared/port_client.py        Direct catalog entity upserts
integrations/<agent>/main.py               One agent execution entry point
```

## Ocean as a future option

Ocean is Port's framework for custom integrations and resource resync. It could
be useful if HypeRadar later needs a continuously deployed, Port-managed source
integration with webhook processors or JQ-configurable mappings. Adopting it
would be a new architecture decision, not a description of today's runtime.

Before adopting Ocean, validate its current SDK and deployment APIs, then decide
whether its resync model is a better fit than the existing agent workflow. Do not
claim Ocean scheduling, Ocean containers, Vercel Python sandboxes, or Ocean JQ
mappings in the current demo.
