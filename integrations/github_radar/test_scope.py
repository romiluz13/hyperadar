"""The scope filter (Q2): AI/dev-tools repos stay, lists, courses and off-scope repos go.

Slugs and descriptions come from the 2026-11-26 trending fixtures where one
fits; the list and course rows are named for what they test.
"""

import pytest

import scope


@pytest.mark.parametrize(
    "slug, description, topics",
    [
        (
            "obra/superpowers",
            "An agentic skills framework & software development methodology that works.",
            [],
        ),
        (
            "elementalsouls/Claude-BugHunter",
            "A Claude Code skill bundle for bug hunting and external red-team work",
            [],
        ),
        (
            "apache/airflow",
            "Apache Airflow - A platform to programmatically author, schedule, and monitor workflows",
            ["machine-learning", "workflow"],
        ),
        ("example/agent-kit", "Of course your agent can ship code.", []),
        (
            "GoogleChrome/modern-web-guidance-src",
            "Keep your coding agent up to date with the latest web best practices",
            [],
        ),
    ],
)
def test_ai_and_dev_tools_repos_are_kept(slug, description, topics):
    assert scope.drop_reason(slug, description, topics) is None


@pytest.mark.parametrize(
    "slug, description",
    [
        ("nginx/nginx", "The official NGINX Open Source repository."),
        ("XiaoYouChR/Ghost-Downloader-3", "The only downloader you need."),
        ("we-promise/sure", "The personal finance app for everyone (by everyone)"),
        ("hieunc229/mailflare", "Email for professionals and teams"),
    ],
)
def test_off_scope_repos_are_dropped_with_the_reason(slug, description):
    assert scope.drop_reason(slug, description, []) == scope.OFF_SCOPE


def test_the_repo_name_alone_does_not_put_a_repo_in_scope():
    assert scope.drop_reason("HKUDS/CLI-Anything", "", []) == scope.OFF_SCOPE


def test_a_readme_keyword_keeps_a_repo_whose_about_line_says_nothing():
    slug = "toeverything/AFFiNE"
    description = (
        "There can be more than Notion and Miro. AFFiNE is a next-gen knowledge base."
    )

    assert scope.drop_reason(slug, description, []) == scope.OFF_SCOPE
    assert (
        scope.drop_reason(
            slug, description, [], readme="AFFiNE AI writes, draws and plans."
        )
        is None
    )


@pytest.mark.parametrize(
    "slug, description, topics, word",
    [
        (
            "e2b-dev/awesome-ai-agents",
            "A list of AI autonomous agents",
            ["ai"],
            "awesome-*",
        ),
        (
            "kamranahmedse/developer-roadmap",
            "Interactive roadmaps, guides and other educational content to help developers grow.",
            [],
            "roadmap",
        ),
        (
            "yangshun/tech-interview-handbook",
            "Curated coding interview preparation materials for busy software engineers",
            [],
            "interview",
        ),
        ("example/agents-tutorial", "Build an AI agent step by step", [], "tutorial"),
        (
            "dair-ai/Prompt-Engineering-Guide",
            "Guides, papers, lessons, notebooks and resources for prompt engineering",
            [],
            "guide",
        ),
        (
            "example/agents-for-beginners",
            "12 lessons on AI agents",
            ["llm", "course"],
            "course",
        ),
    ],
)
def test_lists_and_courses_are_dropped_even_when_they_are_about_ai(
    slug, description, topics, word
):
    reason = scope.drop_reason(slug, description, topics)

    assert reason is not None and reason != scope.OFF_SCOPE
    assert word in reason
