"""What a digest item says about itself: a grounded `what` and a topic emoji.

``grounded_what`` accepts a model-written sentence only when it could have been
copied from the item's own sources: at most 25 words, no number token the
sources do not hold, and no other word outside the sources and a short list of
English function words. A word matches its source with or without one trailing
``s`` or ``es``, so "whiteboards" is grounded by the topic "whiteboard".

The sources are written by strangers, so grounding alone would let Grove copy
an instruction or a payload out of them. ``grounded_what`` and ``safe_blurb``
reject any text with a link (``://``, ``www.``, a domain followed by a path,
an email address, a ``javascript:`` or ``mailto:`` scheme or a Markdown link)
or a markup character (``<``, ``>`` or a backtick). A blurb may run to two
sentences of the same 25-word cap each. Both are capped in characters before
any pattern runs, since one model-written "word" can be any length.

``topic_emoji`` picks one emoji from a fixed topic map, in code; no model
chooses it.
"""

import html
import re
import unicodedata

WHAT_MAX_WORDS = 25
BLURB_MAX_SENTENCES = 2
BLURB_MAX_WORDS = BLURB_MAX_SENTENCES * WHAT_MAX_WORDS
WHAT_MAX_CHARS = 300
BLURB_MAX_CHARS = BLURB_MAX_SENTENCES * WHAT_MAX_CHARS
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
# Chat apps link any name.tld, and no TLD list stays complete, so every
# name.letters shape counts as a link (Fireworks.ai included: it would render as
# one), in any script. File suffixes that are no TLD keep names like Node.js and
# package.json; .md and .py are real TLDs, so AGENTS.md counts as a link.
_FILE_SUFFIXES = "js|ts|jsx|tsx|txt|json|yml|yaml|toml|lock|cfg|ini"
_LINK_OR_MARKUP = re.compile(
    r"://|www\.|\w@[\w-]+\.|\b(?:javascript|mailto):|\]\(|[<>`]"
    rf"|[\w-][.\u3002\uff61](?!(?:{_FILE_SUFFIXES})(?![^\W\d_]))[^\W\d_]{{2}}"
    r"|\b\d{1,3}(?:\.\d{1,3}){3}\b",
    re.IGNORECASE,
)
# Only the first mark of a run may start a match, so "!!!!" stays linear.
_SENTENCE_END = re.compile(r"(?<![.!?])[.!?]+(?=\s|$)")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(html.unescape(text).lower())


def _forms(token: str) -> set[str]:
    forms = {token, token + "s", token + "es"}
    if token.endswith("es"):
        forms.add(token[:-2])
    if token.endswith("s"):
        forms.add(token[:-1])
    return forms


def carries_link_or_markup(text: str) -> bool:
    """Whether ``text`` holds a link shape or a markup character.

    NFKC folds full-width letters and dots to ASCII, and dropping format
    characters (zero-width spaces) stops them splitting a domain unseen.
    """
    folded = unicodedata.normalize("NFKC", text)
    visible = "".join(c for c in folded if unicodedata.category(c) != "Cf")
    return bool(_LINK_OR_MARKUP.search(visible))


def _cleaned(text: object, max_words: int, max_chars: int) -> str | None:
    if not isinstance(text, str) or len(text) > max_chars:
        return None
    cleaned = " ".join(html.unescape(text).split())
    if not 1 <= len(cleaned.split()) <= max_words or carries_link_or_markup(cleaned):
        return None
    return cleaned


def grounded_what(text: object, sources: list[str]) -> str | None:
    """``text`` cleaned when every word is grounded in ``sources``, else None."""
    sentence = _cleaned(text, WHAT_MAX_WORDS, WHAT_MAX_CHARS)
    if sentence is None:
        return None
    known = {token for source in sources for token in _tokens(source)}
    for token in _tokens(sentence):
        if any(char.isdigit() for char in token):
            if token not in known:
                return None
        elif token not in FUNCTION_WORDS and not _forms(token) & known:
            return None
    return sentence


def safe_blurb(text: object) -> str | None:
    """``text`` cleaned: at most two short sentences, with no link or markup."""
    blurb = _cleaned(text, BLURB_MAX_WORDS, BLURB_MAX_CHARS)
    if blurb is None or len(_SENTENCE_END.findall(blurb)) > BLURB_MAX_SENTENCES:
        return None
    return blurb


def _emoji_for(tokens: set[str]) -> str | None:
    for emoji, words in TOPIC_EMOJI:
        if tokens & words:
            return emoji
    return None


def topic_emoji(topics: list[str], text: str) -> str:
    """The first map entry the topics match, else the text's, else 📦."""
    from_topics = _emoji_for({t for topic in topics for t in _tokens(topic)})
    return from_topics or _emoji_for(set(_tokens(text))) or DEFAULT_EMOJI
