import re
from urllib.parse import parse_qs, urlparse


def slug_for_url(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    host = (parsed.hostname or "").lower()

    if host == "github.com" and len(parts) >= 2:
        return f"{clean_slug(parts[0])}-{clean_slug(parts[1])}"

    query = parse_qs(parsed.query)
    if host in {"youtube.com", "www.youtube.com"} and parts[:1] == ["watch"]:
        video_ids = query.get("v")
        if video_ids:
            return f"youtube-{clean_slug(video_ids[0])}"
    if host == "youtu.be" and parts:
        return f"youtube-{clean_slug(parts[0])}"

    if (
        host in {"reddit.com", "www.reddit.com"}
        and len(parts) >= 4
        and parts[0] == "r"
        and parts[2] == "comments"
    ):
        return f"reddit-{clean_slug(parts[1])}-{clean_slug(parts[3])}"

    host = re.sub(r"^www\.", "", host)
    query_identity = (query.get("id") or query.get("v") or [None])[0]
    value = "-".join(str(part) for part in [host, *parts, query_identity] if part)
    return clean_slug(value)[:120]


def clean_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
