"""The AI and dev-tools scope keywords, shared by every gate.

github_radar keeps a trending repo only when one of these appears in its text.
The daily digest keeps a Show HN post when one is in its title or two distinct
ones are in its story. Keywords match whole words after ``scope_words``
lowercases the text and splits ``-_./``, so "Email" is not "ai" and
"AI-powered" is.
"""

import re

_KEYWORDS = (
    r"ai|ml|llms?|gpt|chatgpt|openai|anthropic|claude|gemini|copilot|codex|mcp|rag"
    r"|agents?|agentic|chatbots?|prompts?|inference|embeddings?|vectors?|neural"
    r"|transformers?|diffusion|multimodal|generative|fine[ -]tun(?:e|ed|ing)"
    r"|(?:large )?language models?|machine learning|deep learning"
    r"|developers?|devtools?|dev tools?|cli|sdk|ide|coding|codebase|code review"
    r"|code editor|compilers?|debuggers?|terminal|programming"
)
_KEYWORD_RE = re.compile(rf"\b(?:{_KEYWORDS})\b")
HN_STORY_MIN_KEYWORDS = 2


def scope_words(text: str) -> str:
    return re.sub(r"[-_./]+", " ", text.lower())


def in_scope(text: str) -> bool:
    """Whether an AI or dev-tools keyword appears in ``text``."""
    return bool(_KEYWORD_RE.search(scope_words(text)))


def hn_on_topic(title: str, story_text: str) -> bool:
    """A Show HN post's title names a keyword, or its story names two distinct ones.

    One mention in a story ("with the help of our friend Claude") says how a
    project was built, not what it is. Spellings of one keyword count once.
    """
    found = set(map(_keyword_root, _KEYWORD_RE.findall(scope_words(story_text))))
    return in_scope(title) or len(found) >= HN_STORY_MIN_KEYWORDS


def _keyword_root(match: str) -> str:
    """One root for "dev tools" and "devtools", or "fine tuning" and "fine tune"."""
    joined = match.replace(" ", "").removeprefix("large").removesuffix("s")
    return re.sub(r"tun(?:ed|ing)$", "tune", joined)
