"""Shared infrastructure for all HypeRadar agent-creators.

Each agent (github_radar, reddit_pulse, youtube_trends, hidden_gems, weekly_digest)
imports from this package: MongoDB helpers, Port client, embeddings, and the
shared write_post function that handles the twin-model write (MongoDB + Port).
"""
