"""What a digest item says about itself: a grounded `what` and a topic emoji.

``grounded_what`` accepts a model-written sentence only when it could have been
copied from the item's own sources: at most 25 words, no number token the
sources do not hold, and no other word outside the sources and a short list of
English function words. A word matches its source with or without one trailing
``s`` or ``es``, so "whiteboards" is grounded by the topic "whiteboard".

``topic_emoji`` picks one emoji from a fixed topic map, in code; no model
chooses it.
"""

import html
import re

WHAT_MAX_WORDS = 25
FUNCTION_WORDS = frozenset(
    "a an the of to for and or with that this is are by in on as it its their "
    "from at into your you not".split()
)
DEFAULT_EMOJI = "📦"
# Checked in this order, so a security tool for LLMs is a security tool.
TOPIC_EMOJI = (
    (
        "🔐",
        frozenset(
            "security cybersecurity infosec appsec pentest pentesting "
            "vulnerability vulnerabilities exploit exploits malware cve "
            "redteam bugbounty".split()
        ),
    ),
    (
        "🤖",
        frozenset(
            "agent agents agentic llm llms mcp rag chatgpt gpt openai anthropic "
            "claude copilot chatbot prompt prompts".split()
        ),
    ),
    (
        "🧠",
        frozenset(
            "model models inference diffusion transformer transformers learning "
            "neural pytorch tensorflow finetuning embedding embeddings vision "
            "tts asr ml".split()
        ),
    ),
    (
        "📊",
        frozenset(
            "data database databases sql analytics etl dataset datasets scraping "
            "scraper crawler crawling vector spreadsheet postgres postgresql "
            "mongodb sqlite".split()
        ),
    ),
    (
        "🎨",
        frozenset(
            "ui ux design frontend css react vue svelte component components "
            "figma tailwind tailwindcss".split()
        ),
    ),
    (
        "🛠️",
        frozenset(
            "cli ide devtools developer developers git github terminal editor "
            "compiler debugger testing ci cd sdk framework vscode neovim vim "
            "linter devops docker kubernetes api tool tools".split()
        ),
    ),
)

_TOKEN = re.compile(r"[^\W_]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(html.unescape(text).lower())


def _forms(token: str) -> set[str]:
    forms = {token, token + "s", token + "es"}
    if token.endswith("es"):
        forms.add(token[:-2])
    if token.endswith("s"):
        forms.add(token[:-1])
    return forms


def grounded_what(text: object, sources: list[str]) -> str | None:
    """``text`` cleaned when every word is grounded in ``sources``, else None."""
    if not isinstance(text, str):
        return None
    sentence = " ".join(html.unescape(text).split())
    if not 1 <= len(sentence.split()) <= WHAT_MAX_WORDS:
        return None
    known = {token for source in sources for token in _tokens(source)}
    for token in _tokens(sentence):
        if any(char.isdigit() for char in token):
            if token not in known:
                return None
        elif token not in FUNCTION_WORDS and not _forms(token) & known:
            return None
    return sentence


def _emoji_for(tokens: set[str]) -> str | None:
    for emoji, words in TOPIC_EMOJI:
        if tokens & words:
            return emoji
    return None


def topic_emoji(topics: list[str], text: str) -> str:
    """The first map entry the topics match, else the text's, else 📦."""
    from_topics = _emoji_for({t for topic in topics for t in _tokens(topic)})
    return from_topics or _emoji_for(set(_tokens(text))) or DEFAULT_EMOJI
