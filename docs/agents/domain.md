# Domain Docs

HypeRadar uses a single domain context.

Before changing behavior:

- Read the root `CONTEXT.md` when it exists.
- Read accepted decisions in `docs/adr/` that touch the change.
- Use the glossary's vocabulary when `CONTEXT.md` defines a term.
- Flag any proposal that contradicts an accepted ADR instead of silently
  overriding it.

Create or extend domain documentation only when a durable term or architectural
decision cannot be inferred from the code.
