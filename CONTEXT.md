# HypeRadar domain

HypeRadar finds what AI developers are hyping, and RomBot sends a once-a-day digest of it over WhatsApp. That daily digest is the product; the website is secondary.

## Terms

- **Trending repo**: a repo listed on a github.com/trending page (all languages, or a language page, daily or weekly) on the day it was parsed. Its **trending rank** is its position on that page that day. It is displayed, never scored.
- **Stars this week**: the sum of a repo's last 7 complete days in GitHub's `stargazers/history`, with the day in progress excluded. **Stars today** is the last complete day. The number GitHub prints on the trending page is kept only as a cross-check (`trending_snapshots`).
- **Hype score**: the score that orders repo candidates in the digest (ADR 0007). It is built from stars this week, relative growth, stars today and HN points, and applies to repos only.
- **Momentum score**: hidden-gems' breakout-prediction score, built from star velocity and acceleration over tracked snapshots (ADR 0003). It is not the hype score.
- **Slot**: a place in the daily digest reserved for one kind of item: up to 3 repos, then at most 1 Reddit thread, 1 HN post and 1 video (ADR 0008). Each kind is ranked only within its own kind, and an empty slot stays empty.
- **What**: one plain sentence saying what an item is. It contains no numbers, and no word or name that its sources (About line, README, topics or post body) don't contain.
- **Stat line**: an item's numbers (stars this week, trending rank, upvotes, points, views), taken only from evidence and never written by an LLM.
- **rankScore**: the web feed's ordering score. It does not order the digest.
- **digestScore**: the retired cross-kind digest score, replaced by slots.
