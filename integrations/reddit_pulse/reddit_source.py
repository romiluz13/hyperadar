"""Reddit source — uses Bright Data CLI (bdata) to pull posts from AI subreddits.

Bright Data's reddit_posts pipeline gives structured JSON (title, upvotes,
comments, related posts) without the $12k/yr Reddit API commercial gate.
See docs/reference/source-constraints-and-costs.md.
"""
import asyncio
import json
import logging

# Subreddits to monitor — AI dev focused
SUBREDDITS = [
    "https://www.reddit.com/r/LocalLLaMA/",
    "https://www.reddit.com/r/MachineLearning/",
    "https://www.reddit.com/r/programming/",
    "https://www.reddit.com/r/singularity/",
]


async def fetch_reddit_candidates(max_results: int = 10) -> list[dict]:
    """Fetch trending posts from AI subreddits via bdata.

    Returns normalized candidate dicts. The "project" here is the Reddit thread
    itself (kind=thread), plus any GitHub repos mentioned in related posts.
    """
    candidates = []
    for sub_url in SUBREDDITS[:2]:  # limit to 2 subreddits per run (cost)
        try:
            proc = await asyncio.create_subprocess_exec(
                "bdata", "pipelines", "reddit_posts", sub_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            data = stdout.decode()
            start = data.find("[{")
            if start < 0:
                continue
            posts = json.loads(data[start:])
            if not posts:
                continue
            post = posts[0]  # the main post for this subreddit
            upvotes = post.get("num_upvotes", 0)
            num_comments = post.get("num_comments", 0)
            title = post.get("title", "")
            url = post.get("url", sub_url)

            candidates.append({
                "url": url,
                "title": title[:200],
                "kind": "thread",
                "description": post.get("description", "")[:500],
                "topics": ["reddit", "ai"],
                "upvotes": upvotes,
                "num_comments": num_comments,
                "subreddit": post.get("community_name", ""),
                "stars": upvotes,  # use upvotes as the "momentum" proxy
            })

            # Extract GitHub repos from related posts (hidden gems on Reddit)
            for related in post.get("related_posts", [])[:5]:
                rel_url = related.get("community_url", "")
                if "github.com" in rel_url:
                    candidates.append({
                        "url": rel_url,
                        "title": related.get("title", rel_url)[:200],
                        "kind": "repo",
                        "description": related.get("title", ""),
                        "topics": ["reddit-found", "ai"],
                        "upvotes": int(related.get("num_upvotes", 0) or 0),
                        "num_comments": int(related.get("num_comments", 0) or 0),
                        "stars": int(related.get("num_upvotes", 0) or 0),
                    })
        except Exception as e:
            logging.warning("reddit_source fetch failed for %s: %s", sub_url, e)
            continue

    return candidates[:max_results]
