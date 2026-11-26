"""Tests for the digest item text: the grounded `what` check and the topic emoji.

Seam: ``grounded_what(text, sources)`` and ``topic_emoji(topics, text)`` in
`integrations/_shared/item_text.py`. The worked examples are five repos from
the 2026-11-26 trending page, each with an accepted sentence and a rejected
variant: a number absent from the sources, an absent name or an invented
adjective.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared.item_text import grounded_what, topic_emoji  # noqa: E402

FIRECRAWL = [
    "firecrawl",
    "Knowledge infrastructure to make your agents smarter. Search, scrape and "
    "access 550+ data providers through one API.",
    "You can use Firecrawl three ways. This repo holds the code for Firecrawl "
    "Local and the self-hosted server.",
    # The accepted sentence says "web", which the quoted About line and README
    # excerpt do not hold; this topic is assumed, not fetched.
    "web-scraping",
]
OPENVINO = [
    "openvino",
    "OpenVINO™ is an open source toolkit for optimizing and deploying AI inference",
    "Open-source software toolkit for optimizing and deploying deep learning models.",
]
AFFINE = [
    "AFFiNE",
    "There can be more than Notion and Miro. AFFiNE is a next-gen knowledge base "
    "that brings planning, sorting and creating all together. Privacy first, "
    "open-source.",
    "A privacy-focused, local-first, open-source alternative for Notion & Miro.",
    "knowledge-base notion miro whiteboard",
]
LAST30DAYS = [
    "last30days-skill",
    "AI agent skill that researches any topic across Reddit, X, YouTube, HN, "
    "Polymarket, and the web - then synthesizes a grounded summary",
    "An AI agent-led search engine scored by upvotes, likes, and real money.",
]
OPENSRE = [
    "opensre",
    "The open-source agent for CI/CD reliability.",
    "Your CI tells you it broke, but not why or how to fix it. OpenSRE is a CLI "
    "agent that reads the failed run, traces the cause and pushes a fix for you "
    "to review. Self-hosted.",
]

ACCEPTED = [
    (
        FIRECRAWL,
        "Knowledge infrastructure to make agents smarter: search and scrape web "
        "data through one API.",
    ),
    (
        OPENVINO,
        "Open-source toolkit for optimizing and deploying deep learning and AI "
        "inference models.",
    ),
    (
        AFFINE,
        "A privacy-first, open-source knowledge base that brings planning, "
        "creating and whiteboards together as a Notion and Miro alternative.",
    ),
    (
        LAST30DAYS,
        "AI agent skill that researches any topic across Reddit, X, YouTube, HN, "
        "Polymarket and the web, then synthesizes a grounded summary.",
    ),
    (
        OPENSRE,
        "Open-source CLI agent that reads a failed CI/CD run, traces the cause "
        "and pushes a fix for review.",
    ),
]

REJECTED = [
    # A number the sources do not hold; "550+", which the About line holds,
    # would be kept.
    (FIRECRAWL, "Search and scrape 600+ data providers through one API."),
    (OPENVINO, "Open-source toolkit for AI inference, the fastest inference engine."),
    (AFFINE, "A privacy-first knowledge base, a faster Obsidian."),
    (
        LAST30DAYS,
        "AI agent skill that researches any topic across Hacker News and Twitter.",
    ),
    (OPENSRE, "Open-source CLI agent that saves your team hours every week."),
]


class TestGroundedWhat:
    @pytest.mark.parametrize("sources,sentence", ACCEPTED)
    def test_the_worked_examples_are_accepted(self, sources, sentence):
        assert grounded_what(sentence, sources) == sentence

    @pytest.mark.parametrize("sources,sentence", REJECTED)
    def test_the_rejected_variants_are_rejected(self, sources, sentence):
        assert grounded_what(sentence, sources) is None

    def test_a_number_from_the_sources_is_kept(self):
        sources = ["A Python 3 toolkit for agents"]
        assert grounded_what("A Python 3 toolkit for agents.", sources) is not None

    def test_26_words_is_one_too_many(self):
        words = [f"w{chr(97 + n)}" for n in range(26)]
        sources = [" ".join(words)]
        assert grounded_what(" ".join(words[:25]), sources) is not None
        assert grounded_what(" ".join(words[:26]), sources) is None

    def test_an_empty_or_missing_sentence_is_rejected(self):
        assert grounded_what("", OPENSRE) is None
        assert grounded_what("   ", OPENSRE) is None
        assert grounded_what(None, OPENSRE) is None
        assert grounded_what(42, OPENSRE) is None

    def test_whitespace_and_entities_are_cleaned(self):
        assert (
            grounded_what("  Open-source   CLI\nagent &amp; fix ", OPENSRE)
            == "Open-source CLI agent & fix"
        )


class TestTopicEmoji:
    @pytest.mark.parametrize(
        "topics,text,emoji",
        [
            (["ai-agents", "mcp"], "", "🤖"),
            (["llm"], "", "🤖"),
            (["cli", "developer-tools"], "", "🛠️"),
            (["deep-learning", "inference"], "", "🧠"),
            (["security", "llm"], "", "🔐"),
            (["database", "analytics"], "", "📊"),
            (["react", "design-system"], "", "🎨"),
            (["knowledge-base", "notion"], "", "📦"),
            ([], "", "📦"),
        ],
    )
    def test_topics_pick_the_emoji(self, topics, text, emoji):
        assert topic_emoji(topics, text) == emoji

    def test_with_no_matching_topic_the_text_decides(self):
        assert (
            topic_emoji(["knowledge-base"], "Open-source CLI agent for CI/CD") == "🤖"
        )
        assert topic_emoji([], "A terminal for developers") == "🛠️"

    def test_a_topic_beats_the_text(self):
        assert topic_emoji(["database"], "An agent framework") == "📊"
