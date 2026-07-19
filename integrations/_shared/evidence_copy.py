"""Deterministic public evidence copy built only from observed source values."""


def _number(value: int | float) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def github_evidence_copy(
    average_per_week: int | float, stars: int, sustained: bool
) -> str:
    suffix = (
        "six-week nondecreasing growth was observed."
        if sustained
        else "recent growth was not independently measured."
    )
    return (
        f"AVG {_number(average_per_week)}★/wk since creation. "
        f"{stars:,} GitHub stars observed; {suffix}"
    )


def youtube_evidence_copy(views: int) -> str:
    return (
        f"{views:,} YouTube views observed. Search surfaced this video; "
        "upload-age view velocity was not measured."
    )


def hidden_gem_evidence_copy(source: str, value: int | float) -> str:
    observed = _number(value)
    if source == "hacker_news":
        return (
            f"{observed} HN points observed. Early attention—not GitHub stars or "
            "a proven trajectory."
        )
    return (
        f"{observed} GitHub stars observed in recent-repository search; growth "
        "trajectory was not measured."
    )


def reddit_evidence_copy(num_upvotes: int, num_comments: int) -> str:
    return (
        f"{_number(num_upvotes)} Reddit upvotes observed across "
        f"{_number(num_comments)} comments. Hot-post engagement, not GitHub stars."
    )
