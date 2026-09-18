"""Grove gateway key handling."""

from _shared.grove import grove_api_key


def test_grove_api_key_trims_surrounding_whitespace(monkeypatch):
    """A key pasted into the GH UI with a trailing newline still works."""
    monkeypatch.setenv("GROVE_API_KEY", "abc123\n")
    assert grove_api_key() == "abc123"


def test_grove_api_key_trims_leading_and_trailing_spaces(monkeypatch):
    monkeypatch.setenv("GROVE_API_KEY", "  abc123  ")
    assert grove_api_key() == "abc123"


def test_grove_api_key_missing_returns_empty(monkeypatch):
    """A missing key surfaces as an auth failure, not a header crash."""
    monkeypatch.delenv("GROVE_API_KEY", raising=False)
    assert grove_api_key() == ""
