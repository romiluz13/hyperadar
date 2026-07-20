"""Tests for Voyage 4 Large embedding generation.

Seam: the public `embed()` and `embed_project()` functions in
`integrations/_shared/embeddings.py`. Mocks the Voyage client — no
network calls, no API key needed.
"""

from _shared import embeddings


class _FakeVoyageResult:
    """Mimics the result object returned by voyageai.Client().embed()."""

    def __init__(self, dims: int = 1024):
        self.embeddings = [[0.1] * dims]


class _FakeVoyageClient:
    """Mimics voyageai.Client — captures the call args for assertion."""

    def __init__(self, dims: int = 1024):
        self._dims = dims
        self.captured_texts: list[list[str]] = []
        self.captured_model: list[str] = []
        self.captured_input_type: list[str] = []

    def embed(self, texts, model, input_type):
        self.captured_texts.append(list(texts))
        self.captured_model.append(model)
        self.captured_input_type.append(input_type)
        return _FakeVoyageResult(self._dims)


def test_voyage_embed_returns_1024_dim_vector():
    """Voyage 4 Large embeddings are 1024-dim, replacing MiniLM's 384-dim."""
    original_client = embeddings._client
    embeddings._client = _FakeVoyageClient(dims=1024)
    try:
        vec = embeddings.embed("AI agent security")
        assert len(vec) == 1024
        assert all(isinstance(v, float) for v in vec)
    finally:
        embeddings._client = original_client


def test_voyage_embed_uses_correct_model_and_input_type():
    """embed() must call Voyage with voyage-4-large and input_type=document."""
    fake = _FakeVoyageClient(dims=1024)
    original_client = embeddings._client
    embeddings._client = fake
    try:
        embeddings.embed("test text")
        assert fake.captured_model[0] == "voyage-4-large"
        assert fake.captured_input_type[0] == "document"
    finally:
        embeddings._client = original_client


def test_voyage_embed_project_combines_title_description_and_topics():
    """embed_project should embed a combined text string of the project identity."""
    fake = _FakeVoyageClient(dims=1024)
    original_client = embeddings._client
    embeddings._client = fake
    try:
        vec = embeddings.embed_project(
            "AgentShield", "Security for AI agents", ["ai", "security"]
        )
        assert len(vec) == 1024
        assert len(fake.captured_texts) == 1
        text = fake.captured_texts[0][0]
        assert "AgentShield" in text
        assert "Security for AI agents" in text
        assert "ai" in text
        assert "security" in text
    finally:
        embeddings._client = original_client


def test_voyage_embed_handles_empty_text():
    """Empty or whitespace-only text should use a fallback, not fail."""
    original_client = embeddings._client
    embeddings._client = _FakeVoyageClient(dims=1024)
    try:
        vec = embeddings.embed("")
        assert len(vec) == 1024
        vec2 = embeddings.embed("   ")
        assert len(vec2) == 1024
    finally:
        embeddings._client = original_client
