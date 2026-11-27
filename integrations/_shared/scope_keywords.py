"""The AI and dev-tools scope keywords (decision Q2), shared by every gate.

github_radar keeps a trending repo, and the daily digest keeps a Show HN
post, only when one of these appears in its text. Keywords match whole words
after ``scope_words`` lowercases the text and splits ``-_./``, so "Email" is
not "ai" and "AI-powered" is.
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


def scope_words(text: str) -> str:
    return re.sub(r"[-_./]+", " ", text.lower())


def in_scope(text: str) -> bool:
    """Whether an AI or dev-tools keyword appears in ``text``."""
    return bool(_KEYWORD_RE.search(scope_words(text)))
