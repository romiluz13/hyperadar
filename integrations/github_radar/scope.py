"""The scope filter: which trending repos are AI or developer tools (ADR 0007).

A repo stays when an AI/dev-tools keyword appears in its topics, its About
line or its README excerpt. Lists, courses and guides go even when they are
about AI.
Keywords match whole words, so "Email" is not "ai".
"""

import re

from _shared.scope_keywords import in_scope, scope_words

OFF_SCOPE = "off-scope: no AI/dev-tools keyword in topics, description or README"

# "Of course" is English, not a course.
_COURSE_RE = re.compile(
    r"\b(tutorial|interview|course|roadmap|guide)s?\b(?<!\bof course)"
)


def drop_reason(
    slug: str, description: str, topics: list[str], readme: str = ""
) -> str | None:
    """Why a repo is out of scope, or None when it stays.

    Lists, courses and guides are judged on the repo name, description and topics.
    Returns ``OFF_SCOPE`` when no keyword matched, so a caller that has not
    read the README yet can read it and ask again.
    """
    name = slug.rsplit("/", 1)[-1].lower()
    if name == "awesome" or name.startswith("awesome-"):
        return "list: an awesome-* repo"
    about = " ".join([scope_words(description), *map(scope_words, topics)])
    course = _COURSE_RE.search(f"{scope_words(name)} {about}")
    if course:
        return f"list or course: '{course.group(1)}' in its name, description or topics"
    if in_scope(f"{about} {readme}"):
        return None
    return OFF_SCOPE
