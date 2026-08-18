"""The shared masthead: the stream page's header carried onto the document pages.

The stream page (stage/pages.py) is the reference; stage/header.py reproduces
its identity for /telemetry and /blog. These tests hold the three pages to one
header: the copied CSS rules must stay byte-identical to the stream page's,
and both document pages must embed the shared module rather than a private
header of their own.
"""

from stage import blog_page, header, pages, telemetry_page

POST = {
    "name": "20260817_120000_000000",
    "slug": "20260817_120000_000000",
    "epoch": 1786968000.0,
    "stamp": "2026-08-17 12:00:00 UTC",
    "title": "Hello",
    "html": "<p>body</p>",
    "truncated": False,
}


def test_masthead_markup_carries_the_stage_identity():
    html = header.masthead(
        "TELEMETRY",
        cluster="<b>cluster</b>",
        left='<a href="/">← the stream</a>',
        fill='<span id="strip-life"></span>',
        right='<a href="/blog">the blog →</a>',
    )
    assert html.startswith('<header id="masthead">')
    assert '<span id="wordmark">AURORA</span>' in html
    assert '<span class="vrule" aria-hidden="true"></span>' in html
    assert '<h1 id="page-name">TELEMETRY</h1>' in html
    assert '<div id="mh-end"><b>cluster</b></div>' in html
    assert f'<span id="repo">{header.REPO}</span>' in html
    assert html.index("← the stream") < html.index("strip-life") < html.index("the blog →")


def test_masthead_banner_lands_inside_the_header():
    html = header.masthead("BLOG", banner='<div id="offline" hidden></div>')
    assert html.index('id="offline"') < html.index("</header>")
    assert html.index('id="mh-b"') < html.index('id="offline"')


def test_masthead_css_rules_match_the_stream_pages():
    """Drift guard: the rules copied from the stream page stay byte-identical."""
    for rule in (
        "#wordmark { font: 600 26px/30px var(--sans); letter-spacing: .18em; color: var(--paper); }",
        ".vrule { width: 1px; height: 20px; background: var(--rule-2); align-self: center; flex: none; }",
        "border-bottom: 1px solid var(--rule-2); }",
        "font: 400 13px/18px var(--mono); color: var(--paper-faint);",
    ):
        assert rule in header.MASTHEAD_CSS, rule
        assert rule in pages.STREAM_PAGE_HTML, rule


def test_masthead_keeps_the_touch_targets_and_the_repo_line():
    assert "min-height: 44px" in header.MASTHEAD_CSS
    # Links never wrap mid-text at in-between viewport widths; the fill slot
    # is the one element that gives way.
    rule = header.MASTHEAD_CSS.split("#mh-b a {")[1].split("}")[0]
    assert "white-space: nowrap" in rule and "flex: none" in rule
    # The grid column and both rows may shrink below their content, so the
    # fill slot ellipsizes instead of the rows overflowing the viewport.
    assert "grid-template-columns: minmax(0, 1fr);" in header.MASTHEAD_CSS
    assert (
        "#mh-a { display: flex; align-items: baseline; gap: 22px; min-width: 0; }"
        in header.MASTHEAD_CSS
    )
    assert (
        "#mh-b { display: flex; align-items: center; gap: 26px; min-width: 0; }"
        in header.MASTHEAD_CSS
    )
    assert header.REPO == "github.com/tachyon-beep/aurora"
    assert header.REPO in pages.STREAM_PAGE_HTML


def test_the_telemetry_page_embeds_the_shared_masthead():
    html = telemetry_page.TELEMETRY_PAGE_HTML
    assert header.MASTHEAD_CSS in html
    assert '<span id="wordmark">AURORA</span>' in html
    assert '<h1 id="page-name">TELEMETRY</h1>' in html
    assert header.REPO in html


def test_the_blog_page_embeds_the_shared_masthead():
    html = blog_page.render_page([POST], 1, 1, 1, False)
    assert header.MASTHEAD_CSS in html
    assert '<span id="wordmark">AURORA</span>' in html
    assert '<h1 id="page-name">BLOG</h1>' in html
    assert header.REPO in html
