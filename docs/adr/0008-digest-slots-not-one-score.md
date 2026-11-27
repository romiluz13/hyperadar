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
- **Quality (added 2026-11-27, after the first dry run):** a Reddit, HN or video item whose post verdict is `inflated` or `cooling` never takes a slot. A Reddit or HN item needs a validated `what`; up to 3 candidates per kind are tried before the slot stays empty. A Show HN post must match the AI and dev-tools scope keywords (`_shared/scope_keywords.py`). These rules also bind the 2-item top-up, which skips only the engagement floors.
- **Amended 2026-11-27, after the whole-branch review and the final dry run:**
  - A Show HN post is on topic when its title names a scope keyword, or its story text names two distinct ones (`hn_on_topic`). One "built with Claude" mention no longer qualifies a game.
  - A repo that @github-radar did not post (a hidden gem, a GitHub-linked Show HN) must pass the same rule over its names, About line and topics. @github-radar's own posts already passed its scope filter.
  - The top-up takes at most one item per non-repo kind, so it never adds a second Reddit thread.
  - When star history fails for most repos (`StarHistoryBatchFailed`), no repo qualifies, the digest still goes out with its other items, and it stores `starHistoryFailed: true`. @github-radar's own scan still fails loudly.

## Consequences

- GitHub always leads. A weak day gives a shorter message, not filler.
- Do not merge the kinds back into one score: their signals don't share a scale, and that merge caused the 2026-11-26 digest.

## Amended 2026-11-27: up to 10 items, and a hidden-gem slot

Rom asked for "5 to 10, not only 5" and for hidden gems every day. Before this, a hidden gem reached the digest only past the 100 stars-this-week repo floor, which a young repo almost never has: none of hidden-gems' daily picks reached RomBot.

- **Slots, in order:** up to 5 repos (hype score, 100+ stars this week); up to 2 hidden gems (hidden-gems' own score, no star floor, the repo scope rule and a validated What, never a repo already leading); up to 2 Reddit threads; 1 Show HN post; 1 video.
- **Count:** at most 10 items. When all eleven places fill, the video goes first. The 2-item top-up is unchanged.
- **Every repo needs a What too.** On the first 10-item dry run a fifth repo went out with none (an empty About line, Grove's sentence rejected). A repo without one gives its slot to the next, as a thread does.
- **Candidates:** Grove describes up to two candidates past each slot's capacity, the repo slots included.
- A gem item carries `hiddenGem: true`, so RomBot can mark it.
- The floors stay: a weak day sends fewer than 5 rather than filler.

