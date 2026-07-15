from _shared.slug import project_slug_for_url, slug_for_url


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


def test_project_identifiers_cannot_collide_when_path_boundaries_move():
    first = project_slug_for_url("https://github.com/foo-bar/baz")
    second = project_slug_for_url("https://github.com/foo/bar-baz")

    assert first != second
    assert first.startswith("foo-bar-baz-")
    assert second.startswith("foo-bar-baz-")
    assert len(first.rsplit("-", 1)[-1]) == 16
    assert len(second.rsplit("-", 1)[-1]) == 16
    assert len(first) <= 120
    assert len(second) <= 120
