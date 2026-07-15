# HypeRadar Agent Rules

## Agent skills

### Issue tracker

Release-recovery work is tracked as local Markdown under `.scratch/`. See
`docs/agents/issue-tracker.md`.

### Triage labels

Use the default Matt Pocock triage roles. See
`docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. Read the root `CONTEXT.md` when present and
the relevant accepted decisions in `docs/adr/`. See `docs/agents/domain.md`.

## Release boundary

- Treat Port as the governed control plane and GitHub Actions as compute.
- Treat MongoDB as the evidence and social-state authority.
- Keep a post private until its Port twin and MongoDB publication state converge.
- Never upgrade a source observation into a stronger motion or growth claim.
- Run each Python package from its own frozen `uv` environment.
- Use a test-only MongoDB database named `test_*` or `*_test`.
- Deploy compatible web code before running project-identity migrations.
- Test command gotcha: run the Python suite via the root `integrations/pytest.ini`
  (e.g. `uv run --frozen --project integrations/github_radar pytest
  integrations/_shared integrations/github_radar`). Do not re-add
  `[tool.pytest.ini_options]` to an agent `pyproject.toml` — it pulls the pytest
  rootdir down to that subdir, and pytest does not load `integrations/conftest.py`
  above the rootdir, so the shared `db` fixture vanishes and every Atlas test
  errors with `fixture 'db' not found` in combined runs (but passes in isolation).
