"""Community source — calls the RomBot community-ask API.

Surfaces trending AI agent discussions from the RomBot community corpus.
The API returns a text answer grounded in real community messages.

Requires: ROMBOT_COMMUNITY_ASK_TOKEN env var.
"""

import logging
import os

import httpx

ROMBOT_API_URL = "https://api.rombot.uk/api/community-ask"
SOURCE_QUERY = (
    "What are the top 5 trending topics about AI agents, coding agents, "
    "or agent frameworks discussed in the community recently? "
    "For each topic, provide on a separate line:\n"
    "TOPIC: <topic title>\n"
    "WHO: <who raised it or the community context>\n"
    "SUMMARY: <one sentence summary>\n"
    "CONTRIBUTORS: <estimated number of people who discussed this>\n"
    "Do not include anything else."
)
SOURCE_COMMAND_TIMEOUT_SECONDS = 90


async def fetch_community_candidates(max_results: int = 5) -> list[dict]:
    """Discover trending AI agent discussions from the RomBot community corpus.

    Raises RuntimeError if ROMBOT_COMMUNITY_ASK_TOKEN is not set.
    """
    token = os.environ.get("ROMBOT_COMMUNITY_ASK_TOKEN")
    if not token:
        raise RuntimeError(
            "ROMBOT_COMMUNITY_ASK_TOKEN not set — cannot call community-ask API"
        )

    candidates: list[dict] = []
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
            if not answer:
                return []
            candidates = _parse_community_answer(answer)
    except Exception as e:
        logging.warning("community_source fetch failed: %s", e)
        return []

    return candidates[:max_results]


def _parse_community_answer(answer: str) -> list[dict]:
    """Parse the RomBot API text response into candidate dicts.

    Expects lines with TOPIC:, WHO:, SUMMARY:, CONTRIBUTORS: markers.
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
                try:
                    contributors = int(
                        line.removeprefix("CONTRIBUTORS:").strip().split()[0]
                    )
                except (ValueError, IndexError):
                    contributors = 0
        if not topic:
            continue
        momentum = min(max(contributors * 5, 20), 100)
        candidates.append(
            {
                "url": "https://api.rombot.uk/community",
                "title": topic[:200],
                "kind": "discussion",
                "description": summary or f"Discussed by {who}"[:500],
                "topics": ["community", "ai-agents", "discussion"],
                "who": who,
                "num_contributors": contributors,
                "visibility_score": momentum,
                "evidence_url": "https://api.rombot.uk/community",
            }
        )
    return candidates
