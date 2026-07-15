# Issue tracker: Local Markdown

Issues and specs for this repository live as Markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`.
- The spec is `.scratch/<feature-slug>/spec.md`.
- Implementation issues are separate files under
  `.scratch/<feature-slug>/issues/<NN>-<slug>.md`.
- A `Status:` line records triage state.
- Conversation history is appended under `## Comments`.

When a skill says to publish to the issue tracker, write the corresponding file
under `.scratch/`. Blocking edges are listed by ticket number and title.
