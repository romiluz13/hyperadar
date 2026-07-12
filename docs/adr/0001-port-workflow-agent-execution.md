# ADR 0001: Run HypeRadar agents through Port Workflows and GitHub Actions

Status: Accepted

## Context

HypeRadar's existing Port actions update catalog metadata but do not execute the
Python agents. Calling that path an agent control plane would overstate what the
system does. Port's current Workflows product can govern a run and report status,
while the repository already has locked Python packages suitable for CI runners.

## Decision

Use an admin-only Port self-service Workflow with an active-agent entity input.
The empty `permissions` object is intentional: Port defines absent or empty
Workflow trigger permissions as Admin-only.
The Workflow dispatches `.github/workflows/run-hyperadar-agent.yml` through the
Port GitHub integration and waits for GitHub's result. GitHub Actions provides the
ephemeral runner and runtime secrets. The selected agent writes detailed evidence
to MongoDB and mirrors catalog entities to Port.

## Consequences

- Port becomes the governed entry point and visible audit trail, not the compute
  runtime.
- The workflow file must exist on the default branch before Port can dispatch it.
- Provisioning must receive the organization-specific GitHub integration
  installation ID explicitly.
- GitHub Actions secrets must be configured before the Workflow is published for
  a demo run.
- External actions and source CLIs are pinned, and runtime secrets are scoped to
  validation and agent-execution steps.
- Scheduled execution remains a separate concern; this decision covers the
  on-demand demonstration path.
