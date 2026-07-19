"""Community source — calls the RomBot community-ask API.

Surfaces trending AI agent discussions from the RomBot community corpus.
The API returns a text answer grounded in real community messages with
TOPIC:/WHO:/SUMMARY: markers.

Requires: ROMBOT_COMMUNITY_ASK_TOKEN env var.
"""

import logging
import os

import httpx

ROMBOT_API_URL = "https://api.rombot.uk/api/community-ask"
SOURCE_QUERY = (
    "What are the top 10 most discussed topics in the AI agents community "
    "in the last 2 weeks? For each topic, provide on a separate block:\n"
    "TOPIC: <topic title>\n"
    "WHO: <who raised it or the community context>\n"
    "SUMMARY: <one sentence summary>\n"
    "CONTRIBUTORS: <estimated number of people who discussed this>\n"
    "Separate each topic with a blank line. Do not include anything else."
)
# 150s — the server timeout is being raised from 60s to 120s; give margin.
SOURCE_COMMAND_TIMEOUT_SECONDS = 150


async def fetch_community_candidates(max_results: int = 10) -> list[dict]:
    """Discover trending AI agent discussions from the RomBot community corpus.

    Raises RuntimeError if ROMBOT_COMMUNITY_ASK_TOKEN is not set.
    Returns [] if the API returns an empty answer (timeout/no content) —
    the caller MUST treat empty as "no data," not hallucinate topics.
    """
    token = os.environ.get("ROMBOT_COMMUNITY_ASK_TOKEN")
    if not token:
        raise RuntimeError(
            "ROMBOT_COMMUNITY_ASK_TOKEN not set — cannot call community-ask API"
        )

    try:
        async with httpx.AsyncClient(timeout=SOURCE_COMMAND_TIMEOUT_SECONDS) as client:
            response = await client.post(
                ROMBOT_API_URL,
                json={"message": SOURCE_QUERY},
                headers={
                    "Content-Type": "application/json",
                    "X-Community-Ask-Token": token,
                },
            )
            response.raise_for_status()
            body = response.json()
            answer = body.get("answer", "")
            if not answer or not answer.strip():
                logging.warning(
                    "community-ask API returned empty answer (likely timeout). "
                    "Skipping run — no real topics to publish."
                )
                return []
            candidates = _parse_community_answer(answer)
            if not candidates:
                logging.warning(
                    "community-ask API returned an answer but no TOPIC: markers "
                    "were found. Answer preview: %s",
                    answer[:200],
                )
            return candidates[:max_results]
    except Exception as e:
        logging.warning("community_source fetch failed: %s", e)
        return []


def _parse_contributor_count(raw: str) -> int:
    """Parse a contributor count from formats like '~10-15', '15', '~8'."""
    import re

    # Remove non-digit prefix characters like ~
    cleaned = re.sub(r"[^0-9-]", "", raw)
    if "-" in cleaned:
        # Range like 10-15 — take the upper bound
        parts = cleaned.split("-")
        try:
            return int(parts[-1])
        except (ValueError, IndexError):
            return 0
    try:
        return int(cleaned)
    except ValueError:
        return 0


def _parse_community_answer(answer: str) -> list[dict]:
    """Parse the RomBot API text response into candidate dicts.

    Expects blocks separated by TOPIC: markers, each containing
    WHO:, SUMMARY:, and optional CONTRIBUTORS: lines.
    """
    candidates = []
    blocks = answer.split("TOPIC:")
    for block in blocks[1:]:  # skip preamble before first TOPIC:
        lines = block.strip().split("\n")
        topic = lines[0].strip()
        who = ""
        summary = ""
        contributors = 0
        for line in lines[1:]:
            line = line.strip()
            if line.startswith("WHO:"):
                who = line.removeprefix("WHO:").strip()
            elif line.startswith("SUMMARY:"):
                summary = line.removeprefix("SUMMARY:").strip()
            elif line.startswith("CONTRIBUTORS:"):
                raw = line.removeprefix("CONTRIBUTORS:").strip()
                # The API returns ranges like "~10-15" or "~6-10".
                # Take the upper bound as the contributor estimate.
                contributors = _parse_contributor_count(raw)
        if not topic:
            continue
        momentum = min(max(contributors * 5, 20), 100)
        candidates.append(
            {
                "url": "https://api.rombot.uk/community",
                "title": topic[:200],
                "kind": "discussion",
                "description": (summary or f"Discussed by {who}")[:500],
                "topics": ["community", "ai-agents", "discussion"],
                "who": who,
                "num_contributors": contributors,
                "visibility_score": momentum,
                "evidence_url": "https://api.rombot.uk/community",
            }
        )
    return candidates
