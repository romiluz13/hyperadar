"""Visible text of an untrusted HTML fragment, in bounded, linear time.

READMEs, Reddit bodies, HN stories and HN comments are written by strangers.
Python's ``re`` backtracks, so a tag pattern that may run past the next ``<``
is quadratic on ``<<<<``; this one stops there, and the input is cut to
``PLAIN_TEXT_MAX_CHARS`` before it runs.
"""

import html
import re

PLAIN_TEXT_MAX_CHARS = 20_000
_TAG = re.compile(r"<[^<>]*>")


def plain_text(value: object) -> str:
    """Tags stripped, entities decoded, whitespace collapsed; "" for a non-string."""
    if not isinstance(value, str):
        return ""
    bounded = value[:PLAIN_TEXT_MAX_CHARS]
    return " ".join(html.unescape(_TAG.sub(" ", bounded)).split())
