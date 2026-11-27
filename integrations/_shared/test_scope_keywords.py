"""Tests for the scope keywords' Show HN rule.

Seam: ``hn_on_topic(title, story_text)`` in `integrations/_shared/scope_keywords.py`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared.scope_keywords import hn_on_topic, in_scope  # noqa: E402

# 2026-11-27 dry run of 4726c55: the opening the lead quoted, then the game as
# the stored `what` describes it. The full story text was not available here.
SYNTH_TITLE = "Show HN: Synth Patch Hero"
SYNTH_STORY = (
    "With the help of our friend Claude, I put together a game that runs "
    "through exercises to help you learn as you build synth patches on a "
    "modular rack."
)


def test_a_game_built_with_claude_is_not_on_topic():
    assert in_scope(f"{SYNTH_TITLE} {SYNTH_STORY}")
    assert not hn_on_topic(SYNTH_TITLE, SYNTH_STORY)


def test_an_ai_title_is_on_topic_whatever_the_story():
    assert hn_on_topic("Show HN: An AI tutor for synth patches", SYNTH_STORY)


def test_two_distinct_story_keywords_are_on_topic():
    story = "It is an agent that speaks MCP, so your editor can drive the rack."
    assert hn_on_topic("Show HN: Patchbay", story)


def test_one_keyword_repeated_or_pluralised_is_still_one():
    assert not hn_on_topic("Show HN: Patchbay", "An agent, and agents, and agents.")


def test_spellings_of_one_keyword_are_still_one():
    assert not hn_on_topic("Show HN: Patchbay", "We fine-tune it; fine-tuning is slow.")
    assert not hn_on_topic("Show HN: Patchbay", "Our devtools, the dev tools we use.")
