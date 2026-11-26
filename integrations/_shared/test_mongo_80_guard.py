"""The code runs on MongoDB 8.0 (Atlas Free): no query feature newer than 8.0.

The list comes from the 8.3 release notes (the 8.1 and 8.2 notes are not
published; ticket 09 Comments) plus $rankFusion and $rerank from the Free fit
check. Comments and docstrings may name a feature; only code is checked.
"""

import io
import re
import tokenize
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FEATURES = [
    ("$scoreFusion", re.compile(r"\$scoreFusion\b")),
    ("$rankFusion", re.compile(r"\$rankFusion\b")),
    ("$rerank", re.compile(r"\$rerank\b")),
    ("$$IDX", re.compile(r"\$\$IDX\b")),
    ("arrayIndexAs", re.compile(r"\barrayIndexAs\b")),
    ("$toArray", re.compile(r"\$toArray\b")),
    ("$toObject", re.compile(r"\$toObject\b")),
    ("$convert with base", re.compile(r"\$convert\b[^}]*?\bbase\b")),
]
_SCANNED = [
    ("apps/web", {".ts", ".tsx"}, {"node_modules", ".next"}),
    ("integrations", {".py"}, {".venv", "__pycache__"}),
    ("scripts", {".py"}, {".venv", "__pycache__"}),
]


_TS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# `//` after `:` is a URL, not a comment.
_TS_LINE_COMMENT = re.compile(r"(^|[^:])//.*$", re.MULTILINE)
_PY_STATEMENT_START = {
    tokenize.NEWLINE,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.ENCODING,
}


def _blank(text: str) -> str:
    return re.sub(r"[^\n]", " ", text)


def _python_code(text: str) -> str:
    """``text`` with comments and bare string statements (docstrings) blanked."""
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    tokens = [
        token
        for token in tokenize.generate_tokens(io.StringIO(text).readline)
        if token.type != tokenize.NL
    ]
    spans = []
    previous = tokenize.NEWLINE
    for index, token in enumerate(tokens):
        following = tokens[index + 1].type if index + 1 < len(tokens) else None
        if token.type == tokenize.COMMENT or (
            token.type == tokenize.STRING
            and previous in _PY_STATEMENT_START
            and following in {tokenize.NEWLINE, tokenize.ENDMARKER}
        ):
            start = offsets[token.start[0] - 1] + token.start[1]
            end = offsets[token.end[0] - 1] + token.end[1]
            spans.append((start, end))
        if token.type != tokenize.COMMENT:
            previous = token.type
    for start, end in reversed(spans):
        text = text[:start] + _blank(text[start:end]) + text[end:]
    return text


def _code(path: Path) -> str:
    text = path.read_text(errors="replace")
    if path.suffix == ".py":
        return _python_code(text)
    text = _TS_BLOCK_COMMENT.sub(lambda match: _blank(match.group()), text)
    return _TS_LINE_COMMENT.sub(r"\1", text)


def post_80_features(root: Path) -> list[str]:
    """Every ``file:line: feature`` under ``root`` that MongoDB 8.0 lacks."""
    found = []
    for directory, suffixes, skipped in _SCANNED:
        for path in sorted((root / directory).rglob("*")):
            relative = path.relative_to(root)
            if (
                path.suffix not in suffixes
                or not path.is_file()
                or skipped & set(relative.parts)
                or path.resolve() == Path(__file__).resolve()
            ):
                continue
            code = _code(path)
            hits = []
            for feature, pattern in _FEATURES:
                for match in pattern.finditer(code):
                    line = code.count("\n", 0, match.start()) + 1
                    hits.append((line, feature))
            found += [f"{relative}:{line}: {feature}" for line, feature in sorted(hits)]
    return found


def test_the_repo_uses_no_query_feature_newer_than_mongodb_80():
    assert post_80_features(_REPO) == []


def _plant(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_a_planted_rank_fusion_stage_fails_the_guard(tmp_path):
    _plant(
        tmp_path,
        "apps/web/lib/feed.ts",
        "const pipeline = [\n  { $rankFusion: { input: { pipelines: {} } } },\n];\n",
    )

    assert post_80_features(tmp_path) == ["apps/web/lib/feed.ts:2: $rankFusion"]


def test_each_listed_feature_in_code_fails_the_guard(tmp_path):
    _plant(
        tmp_path,
        "integrations/agent/query.py",
        "\n".join(
            [
                'a = [{"$scoreFusion": {}}]',
                'b = {"$map": {"input": "$xs", "arrayIndexAs": "i", "in": "$$IDX"}}',
                'c = {"$toArray": "$raw"}',
                'd = {"$toObject": "$raw"}',
                'e = [{"$rerank": {}}]',
                'f = {"$convert": {"input": "$hex", "to": "int", "base": 16}}',
                'g = {"$convert": {"input": "$n", "to": "string"}}',
                'h = {"$objectToArray": "$doc", "$arrayToObject": "$pairs"}',
            ]
        ),
    )

    assert post_80_features(tmp_path) == [
        "integrations/agent/query.py:1: $scoreFusion",
        "integrations/agent/query.py:2: $$IDX",
        "integrations/agent/query.py:2: arrayIndexAs",
        "integrations/agent/query.py:3: $toArray",
        "integrations/agent/query.py:4: $toObject",
        "integrations/agent/query.py:5: $rerank",
        "integrations/agent/query.py:6: $convert with base",
    ]


def test_comments_dependencies_and_build_output_are_not_checked(tmp_path):
    _plant(
        tmp_path,
        "scripts/setup.py",
        '"""Builds the $text leg of $rankFusion."""\n\n# $scoreFusion later\nx = 1\n',
    )
    _plant(
        tmp_path,
        "apps/web/app/page.tsx",
        "// merge like $rankFusion\n/* $rerank\n   too */\nconst url = 'https://x';\n",
    )
    _plant(tmp_path, "apps/web/node_modules/pkg/index.ts", "{ $rankFusion: {} }\n")
    _plant(tmp_path, "apps/web/.next/server/page.ts", "{ $rankFusion: {} }\n")
    _plant(tmp_path, "integrations/agent/.venv/lib/x.py", '{"$rankFusion": {}}\n')

    assert post_80_features(tmp_path) == []
