"""Embedding generation for project vector search.

Uses Voyage AI (voyage-4-large, 1024 dims) via the voyageai Python SDK.
The API key is a MongoDB Atlas Voyage AI key (al- prefix) which routes
to https://ai.mongodb.com/v1 instead of api.voyageai.com.
"""

import logging
import os
from collections.abc import Sequence

_MODEL = "voyage-4-large"
# al- prefix keys route to MongoDB Atlas's Voyage AI endpoint, not api.voyageai.com.
_BASE_URL = "https://ai.mongodb.com/v1"
_client = None


def _get_client():
    """Lazy-init the Voyage client so it's only created when first needed."""
    global _client
    if _client is None:
        import voyageai  # type: ignore[import-untyped]

        api_key = os.environ.get("VOYAGE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "VOYAGE_API_KEY not set — cannot generate Voyage embeddings"
            )
        _client = voyageai.Client(  # type: ignore[attr-defined]
            api_key=api_key,
            base_url=_BASE_URL,
        )
    return _client


def embed(text: str, input_type: str = "document") -> list[float]:
    """Generate a 1024-dim embedding from text using Voyage 4 Large."""
    if not text or not text.strip():
        text = "unknown project"
    try:
        result = _get_client().embed(
            [text],
            model=_MODEL,
            input_type=input_type,
        )
        return [float(v) for v in result.embeddings[0]]
    except Exception as e:
        logging.warning("Voyage embed failed: %s", e)
        raise


def embed_project(title: str, description: str, topics: Sequence[str]) -> list[float]:
    """Embed a project from its title + description + topics (the semantic identity)."""
    text = f"{title}. {description}. Topics: {', '.join(topics)}"
    return embed(text, input_type="document")
