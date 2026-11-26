# ADR 0008: The daily digest is composed by slots, not one cross-kind score

**Date:** 2026-11-26
**Status:** Accepted. Grilled plan, `.scratch/hyperadar-refactor/spec.md`.

## Context

The digest ranked every kind of item on one `digestScore`, where Reddit upvotes and GitHub star gain shared a log scale. On 2026-11-26 that put two Reddit image posts and a repeated title above both repos. RomBot keeps only the first four items, so a repo was cut.

## Decision

- **Slots:** the digest fills slots in a fixed order (`CONTEXT.md`, Slot):
  1. up to 3 repos, ordered by the hype score;
  2. at most 1 Reddit thread, which needs real text or a GitHub link and is never an image, video or meme;
  3. at most 1 non-GitHub Show HN post;
  4. at most 1 video.
- **Floors:** each kind has its own floor and is ranked only within its kind. An empty slot stays empty, and a repo slot is never refilled with another kind.
- **Count:** at most 5 items (the video goes first when all six qualify), and at least 2.

## Consequences

- GitHub always leads. A weak day gives a shorter message, not filler.
- Do not merge the kinds back into one score: their signals don't share a scale, and that merge caused the 2026-11-26 digest.
