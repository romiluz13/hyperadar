"""Embedding generation for project vector search.

Uses sentence-transformers (all-MiniLM-L6-v2, 384 dims) locally — free, offline.
Atlas automated embedding is a possible future replacement; the current runtime
stores these local vectors and queries them with $vectorSearch.
"""

# Lazy-loaded so the model downloads only when first needed.
_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer  # pyright: ignore[reportMissingImports]

        _MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    return _MODEL


def embed(text: str) -> list[float]:
    """Generate a 384-dim embedding from text."""
    if not text or not text.strip():
        text = "unknown project"
    vec = _model().encode(text, normalize_embeddings=True)
    return vec.tolist()


def embed_project(title: str, description: str, topics: list[str]) -> list[float]:
    """Embed a project from its title + description + topics (the semantic identity)."""
    text = f"{title}. {description}. Topics: {', '.join(topics)}"
    return embed(text)
