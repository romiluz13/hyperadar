# ADR 0005: The Port catalog mirror is best effort; MongoDB alone gates publication

**Date:** 2026-11-09
**Status:** Accepted. Supersedes the fail-closed Port publication rule in
`docs/reference/cross-cutting-patterns.md` §1.

## Context

Publication was fail-closed on Port: a post stayed `pending` (hidden) until every
Port upsert succeeded, and each agent run first required a successful Port agent
sync. On 2026-10-29 Port authentication started returning 401. Every run aborted
before scanning, nothing reached MongoDB, and the daily digest (and RomBot's
daily WhatsApp draft) repeated the Oct 29 data for 11 days. No alert fired.

The site and `/api/daily-digest` read MongoDB only. Port is a catalog mirror for
the operator, not a data source for any reader.

## Decision

Port calls on the publication path (`runner.py` agent sync and success record,
`write_post.py` twin sync and multi-source reconciliation) report failures with
`port_client.warn_on_failure`: a `WARNING: Port … failed` line in the run log,
and publication continues. `portSyncStatus: "synced"` now means "published"; it
no longer proves a Port twin exists. A Port login failure returns an
`auth_failed` result instead of raising. After the first auth, network, 429 or
5xx failure, the rest of that agent run skips Port, so a hanging Port cannot
stall the run on 30-second timeouts.

Operator scripts that exist to change Port (`scripts/migrate_publication_state.py`)
keep `require_success`, because there a Port failure is the failure.

## Consequences

- A Port outage no longer freezes the feed. The Port catalog can drift until the
  next successful sync of the same entity.
- The embedding audit and the MongoDB lease and transaction still gate
  publication.
- Run health no longer depends on Port; the daily refresh alert covers agent and
  digest failures.
