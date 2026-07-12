from _shared.slug import slug_for_url


def test_project_url_slugs_match_the_public_web_routes():
    assert slug_for_url("https://github.com/modiqo/skillspec") == "modiqo-skillspec"
    assert (
        slug_for_url("https://www.youtube.com/watch?v=rp5EwOogWEw")
        == "youtube-rp5ewoogwew"
    )
    assert slug_for_url("https://youtu.be/ElYxdpYi4U0") == "youtube-elyxdpyi4u0"
    assert (
        slug_for_url(
            "https://www.reddit.com/r/LocalLLaMA/comments/abc123/a_real_thread/"
        )
        == "reddit-localllama-abc123"
    )
    assert slug_for_url("hyperadar://digest/2026-W27") == "digest-2026-w27"
