"""Grove gateway (Azure APIM) access helpers.

The GitHub Actions web UI stores pasted secrets verbatim, including any
trailing newline. A newline is an illegal HTTP header value, so httpx
fails with ``LocalProtocolError: Illegal header value`` before the request
is ever sent. Always read the key through :func:`grove_api_key`.
"""

import os


def grove_api_key() -> str:
    """Return the Grove subscription key with surrounding whitespace trimmed."""
    return os.environ.get("GROVE_API_KEY", "").strip()
