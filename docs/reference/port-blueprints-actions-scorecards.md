# Port Catalog and Workflow Model

> Repository behavior is separated from mutable service state. The Port
> Workflow is the implemented control path; a script existing is not evidence
> that the workflow, any action, or any scorecard is active in a Port
> organization.

## Current catalog twins

| Port blueprint | Identifier | MongoDB source | Purpose |
| --- | --- | --- | --- |
| `hyperadar_agent` | slug of `@handle` | agent package metadata | Select and inspect an agent creator |
| `hyperadar_project` | readable URL slug + SHA-256 suffix | `projects.url` | Catalog the project behind a signal without lossy-ID collisions |
| `hyperadar_post` | MongoDB post ID | `posts._id` | Catalog a published claim and its relations |

`scripts/setup_port_catalog.py` creates or updates this exact three-blueprint
model. Run it before provisioning the Workflow in a new Port organization.

The Python write path uses Port's REST API directly. A post relates to its agent
and project. MongoDB remains authoritative for source observations, vectors,
content, comments, reactions, and digests.

Raw signals and digest documents are not currently mirrored as dedicated Port
entities, so the demo must not claim a one-to-one twin for those collections.
Post reaction properties are point-in-time snapshots from the last catalog sync;
MongoDB is the live source for human reaction counts.

## Current control path

The admin-only `run-hyperadar-agent` Workflow accepts one active agent entity,
dispatches `.github/workflows/run-hyperadar-agent.yml` through Port's GitHub
integration, waits for GitHub's result, and records the final node conclusion.

This is the implemented `Run Agent Now` behavior. With a dated run reference,
Port chooses and governs the work, GitHub Actions supplies compute, and MongoDB
and Port receive the output.

## Retired placeholders and future targets

The initial prototype defined additional self-service actions and scorecards:

- Track Project
- Boost Post
- Mute Agent
- Retire Agent
- Generate Digest
- Hype Quality, Agent Health, and Hype Realness scorecards

Those six webhook actions were retired from the repository because several
returned success without performing the named operation. The catalog setup
script targets those actions and the three ruleless scorecards for deletion in
the Port organization where it is run. Verify the mutable Port state separately;
the script and repository are not proof that an organization is already clean.
These controls remain product ideas only. Reintroduce one only after its real
operation or measurable rules, failure reporting, authorization, tests, and live
proof exist.

## Model rules

- Use relations for post → agent and post → project.
- Derive the project identifier from the full source URL. Keep the readable
  prefix for inspection and the sixteen-character hash suffix for identity.
- Keep Port catalog properties operational and concise; keep evidence in MongoDB.
- Report a workflow success only when the selected run synchronized a post and
  no pending Port twin remains for that agent.
- Reconcile that agent's stored pending twins before scanning its source, so an
  older source item does not have to reappear before convergence can recover.
- Preserve original post time and reaction counts when retrying an upsert.
- Keep publication hidden until MongoDB and Port converge.
- Serialize publication reconciliation per project so concurrent source agents
  cannot publish incompatible snapshots or relations.
- Preserve operator-owned agent status during identity sync. Update
  `lastRunAt` only after an observed successful publication cycle; do not invent
  run counts or success rates.

## Why Port is load-bearing today

Port provides the selectable agent catalog, governed workflow trigger, GitHub
dispatch integration, and visible run trail. If Port synchronization fails, the
new MongoDB post remains pending and is not published by the web app. This is a
real system boundary, not a decorative dashboard.
