from stage import blog_page

POST = {
    "name": "20260817_120000_000000",
    "slug": "20260817_120000_000000",
    "epoch": 1786968000.0,
    "stamp": "2026-08-17 12:00:00 UTC",
    "title": "Hello <world>",
    "html": '<h1 id="p20260817_120000_000000-h1">Hello &lt;world&gt;</h1>\n<p>body</p>',
    "truncated": False,
}


def test_page_carries_articles_nav_and_strip_links():
    html = blog_page.render_page([POST], 1, 1, 1, False)
    assert "<!doctype html>" in html.lower()
    assert '<article id="post-20260817_120000_000000"' in html
    assert '<a href="#post-20260817_120000_000000">Hello &lt;world&gt;</a>' in html
    assert "<p>body</p>" in html
    assert 'href="/"' in html and 'href="/telemetry"' in html
    assert "1 post" in html
    assert "2026-08-17 12:00:00 UTC" in html


def test_strip_links_carry_directional_arrows():
    """The strip's two links read like the stream page's and the telemetry
    panel's: the leftmost carries a left arrow, the rightmost a right one."""
    html = blog_page.render_page([POST], 1, 1, 1, False)
    assert '<a href="/">← the stream</a>' in html
    assert '<a href="/telemetry">telemetry →</a>' in html


def test_page_pins_mermaid_with_integrity_and_strict_security():
    html = blog_page.render_page([POST], 1, 1, 1, False)
    assert blog_page.MERMAID_URL.startswith("https://cdn.jsdelivr.net/npm/mermaid@11.16.1/")
    assert f'src="{blog_page.MERMAID_URL}"' in html
    assert f'integrity="{blog_page.MERMAID_INTEGRITY}"' in html
    assert 'crossorigin="anonymous"' in html
    assert 'securityLevel: "strict"' in html
    assert "startOnLoad: true" in html


def test_page_pagination_links():
    html = blog_page.render_page([POST], 2, 3, 25, False)
    assert 'href="/blog?page=1"' in html and "newer" in html
    assert 'href="/blog?page=3"' in html and "older" in html
    assert "page 2 of 3" in html
    assert "25 posts" in html
    first = blog_page.render_page([POST], 1, 3, 25, False)
    assert 'href="/blog?page=1"' not in first
    last = blog_page.render_page([POST], 3, 3, 25, False)
    assert 'href="/blog?page=4"' not in last


def test_page_empty_state_and_truncation_notes():
    html = blog_page.render_page([], 1, 1, 0, False)
    assert "Nothing posted." in html
    html = blog_page.render_page([dict(POST, truncated=True)], 1, 1, 1, True)
    assert "cut at" in html
    assert "older posts are not listed" in html


def test_page_never_carries_the_title_unescaped():
    html = blog_page.render_page([dict(POST, title="<script>x</script>")], 1, 1, 1, False)
    assert "<script>x</script>" not in html
