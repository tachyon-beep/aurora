import os
import re
import time

import pytest

from stage import blog


def test_inline_escapes_html_and_keeps_text():
    assert blog.render_inline("a <b>b</b> & c") == "a &lt;b&gt;b&lt;/b&gt; &amp; c"


def test_inline_bold_em_and_code():
    assert blog.render_inline("**bold** and *em* and _em2_ and `co<de>`") == (
        "<strong>bold</strong> and <em>em</em> and <em>em2</em> and <code>co&lt;de&gt;</code>"
    )


def test_inline_code_wins_over_emphasis():
    assert blog.render_inline("`*not em*`") == "<code>*not em*</code>"


def test_inline_underscores_inside_words_are_not_emphasis():
    assert blog.render_inline("snake_case_name") == "snake_case_name"


def test_inline_links_with_safe_schemes():
    assert blog.render_inline("[x](https://e.com/a?b=1&c=2)") == (
        '<a href="https://e.com/a?b=1&amp;c=2" rel="noopener nofollow">x</a>'
    )
    assert blog.render_inline("[m](mailto:a@b.c)") == (
        '<a href="mailto:a@b.c" rel="noopener nofollow">m</a>'
    )
    assert blog.render_inline("[t](#top)") == '<a href="#top" rel="noopener nofollow">t</a>'


def test_inline_unsafe_links_render_as_text():
    for url in (
        "javascript:alert(1)",
        "data:text/html,x",
        "vbscript:x",
        "file:///etc/passwd",
        "//e.com",
    ):
        out = blog.render_inline(f"[click]({url})")
        assert "<a" not in out
        assert "click" in out


def test_inline_link_attribute_escaping():
    out = blog.render_inline('[x](https://e.com/"onmouseover="alert(1))')
    assert 'onmouseover="' not in out
    assert "&quot;" in out


def test_inline_images_render_as_links_never_img():
    assert blog.render_inline("![a cat](https://e.com/cat.png)") == (
        '<a href="https://e.com/cat.png" rel="noopener nofollow">image: a cat</a>'
    )
    out = blog.render_inline("![x](javascript:1)")
    assert "<img" not in out and "<a" not in out


def test_safe_href():
    assert blog.safe_href(" https://x ") == "https://x"
    assert blog.safe_href("HTTP://x") == "HTTP://x"
    assert blog.safe_href("#a") == "#a"
    assert blog.safe_href("javascript:x") is None
    assert blog.safe_href("") is None


def test_inline_pathological_inputs_render_in_linear_time():
    cases = (
        "`" * 20000 + "a",
        "[" * 20000,
        "[a](" * 5000 + "b" * 20000,
        "**" * 10000 + "a",
        "*" * 20000,
        "_" * 20000,
        "![" * 10000,
        "``" + "a" * 20000 + "`" + "b" * 20000,
    )
    for text in cases:
        start = time.perf_counter()
        blog.render_inline(text)
        assert time.perf_counter() - start < 1.0, text[:12]


def test_headings_with_ids_from_prefix_and_index():
    out = blog.render_markdown("# One\n\ntext\n\n### Three <b>\n", id_prefix="p1-")
    assert '<h1 id="p1-h1">One</h1>' in out
    assert '<h3 id="p1-h2">Three &lt;b&gt;</h3>' in out
    assert "<p>text</p>" in out


def test_headings_without_prefix_have_no_id():
    assert blog.render_markdown("## A") == "<h2>A</h2>"


def test_paragraphs_join_lines_and_escape_raw_html():
    out = blog.render_markdown("<script>alert(1)</script>\nsecond line\n\n<div>x</div>")
    assert "<script>" not in out
    assert "<div>" not in out
    assert out == (
        "<p>&lt;script&gt;alert(1)&lt;/script&gt;\nsecond line</p>\n<p>&lt;div&gt;x&lt;/div&gt;</p>"
    )


def test_fenced_code_is_escaped_and_language_restricted():
    out = blog.render_markdown("```python\nprint('<hi>')\n```\n")
    assert out == '<pre><code class="lang-python">print(&#x27;&lt;hi&gt;&#x27;)</code></pre>'
    out = blog.render_markdown('```x" onclick="y\ncode\n```')
    assert "onclick" not in out
    assert out == "<pre><code>code</code></pre>"


def test_mermaid_fence_becomes_a_mermaid_pre():
    out = blog.render_markdown("```mermaid\ngraph TD; A-->B\n```")
    assert out == '<pre class="mermaid">graph TD; A--&gt;B</pre>'


def test_unclosed_fence_runs_to_the_end():
    out = blog.render_markdown("```\nline1\nline2")
    assert out == "<pre><code>line1\nline2</code></pre>"


def test_tilde_fences_and_longer_fences():
    assert blog.render_markdown("~~~\nx\n~~~") == "<pre><code>x</code></pre>"
    assert blog.render_markdown("````\n```\n````") == "<pre><code>```</code></pre>"


def test_unordered_and_ordered_lists_with_nesting():
    src = "- a\n- b\n  - b1\n  - b2\n- c\n\n1. one\n2. two"
    out = blog.render_markdown(src)
    assert out == (
        "<ul><li>a</li><li>b<ul><li>b1</li><li>b2</li></ul></li><li>c</li></ul>\n"
        "<ol><li>one</li><li>two</li></ol>"
    )


def test_ordered_list_start_number():
    assert blog.render_markdown("3. c\n4. d") == '<ol start="3"><li>c</li><li>d</li></ol>'


def test_list_items_render_inline_markdown():
    assert blog.render_markdown("* **b** <x>") == "<ul><li><strong>b</strong> &lt;x&gt;</li></ul>"


def test_blockquotes_render_nested_markdown():
    out = blog.render_markdown("> # q\n> line\n>\n> - i")
    assert out == "<blockquote><h1>q</h1>\n<p>line</p>\n<ul><li>i</li></ul></blockquote>"


def test_horizontal_rules():
    assert blog.render_markdown("a\n\n---\n\nb") == "<p>a</p>\n<hr>\n<p>b</p>"
    assert blog.render_markdown("***") == "<hr>"


def test_pipe_tables():
    src = "| h1 | h2 |\n|----|:--:|\n| a | <b> |\n| c | d |"
    out = blog.render_markdown(src)
    assert out == (
        "<table><thead><tr><th>h1</th><th>h2</th></tr></thead>"
        "<tbody><tr><td>a</td><td>&lt;b&gt;</td></tr><tr><td>c</td><td>d</td></tr></tbody></table>"
    )


def test_table_without_separator_is_a_paragraph():
    assert blog.render_markdown("| a | b |\n| c | d |") == "<p>| a | b |\n| c | d |</p>"


def test_a_table_interrupts_a_paragraph():
    """A table is a block start, so a header row must end the paragraph above it
    rather than being swallowed into it as text."""
    out = blog.render_markdown("intro\n| h1 | h2 |\n| -- | -- |\n| a | b |")
    assert out.startswith("<p>intro</p>")
    assert "<table>" in out


def test_a_rule_under_prose_holding_a_pipe_is_not_a_table():
    """GFM matches the delimiter row's cell count against the header's. Without
    that check any line carrying a pipe followed by a rule becomes a table, and
    a table interrupting a paragraph makes that reachable from ordinary text."""
    assert blog.render_markdown("weighed a | b\n---\nrest") == (
        "<p>weighed a | b</p>\n<hr>\n<p>rest</p>"
    )


def test_crlf_and_blank_lines_are_tolerated():
    assert blog.render_markdown("a\r\n\r\n\r\nb\r\n") == "<p>a</p>\n<p>b</p>"
    assert blog.render_markdown("") == ""
    assert blog.render_markdown("\n\n") == ""


def test_first_heading():
    assert blog.first_heading("intro\n\n# The *title*\n\n## sub") == "The *title*"
    assert blog.first_heading("no heading") is None
    assert blog.first_heading("```\n# not a heading\n```\n# real") == "real"


def test_deeply_nested_blocks_render_without_recursion_or_delay():
    cases = (
        ">" * 3000 + " a",
        "> " * 5000 + "a",
        "".join("  " * i + "- a\n" for i in range(2000)),
        "".join(">" * i + " q\n" for i in range(1, 400)),
    )
    for index, text in enumerate(cases):
        start = time.perf_counter()
        out = blog.render_markdown(text)
        assert time.perf_counter() - start < 1.0, text[:12]
        assert "<script" not in out
        if index == 0:
            assert out.count("<blockquote>") <= blog.MAX_NESTING + 1
        if index == 2:
            assert out.count("<ul>") <= blog.MAX_NESTING + 1


def test_nesting_up_to_the_cap_is_structural_and_beyond_it_is_text():
    deep = ">" * (blog.MAX_NESTING + 3) + " a"
    out = blog.render_markdown(deep)
    assert out.count("<blockquote>") == blog.MAX_NESTING + 1
    assert "&gt;&gt; a" in out


def test_render_output_is_bounded_relative_to_input():
    """No construct may turn a post into more than a small constant multiple of
    its own size, and each must render inside a second: the page is public and
    the posts are agent-authored."""
    wide_table = "|" + "h|" * 6000 + "\n|" + "-|" * 3 + "\n" + "|a|\n" * 3500
    cases = (
        wide_table,
        "|" * 30000 + "\n" + "|-" * 3 + "\n" + ("|" * 30000 + "\n") * 2,
        ("- a\n" * 8000),
        "".join("  " * (i % 20) + "- a\n" for i in range(3000)),
        ("> " * 16 + "a\n") * 1500,
        ("# a\n") * 12000,
        ("**a** *b* `c` [d](https://e) ![f](https://g)\n") * 1200,
        ("```\n" + "x\n" + "```\n") * 6000,
    )
    prefix = "p" + "x" * blog.SLUG_MAX + "-"
    for text in cases:
        start = time.perf_counter()
        out = blog.render_markdown(text, id_prefix=prefix)
        assert time.perf_counter() - start < 1.0, text[:20]
        assert len(out) <= 32 * len(text) + 1024, (text[:20], len(text), len(out))


def _blog(tmp_path):
    diode = tmp_path / "diode"
    (diode / "blog").mkdir(parents=True)
    return diode


def test_list_posts_missing_folder(tmp_path):
    assert blog.list_posts(str(tmp_path / "nowhere")) == ([], False)


def test_list_posts_newest_first_md_only_and_capped(tmp_path, monkeypatch):
    diode = _blog(tmp_path)
    for stem in ("20260817_100000_000000", "20260817_120000_000000", "20260817_110000_000000"):
        (diode / "blog" / f"{stem}.md").write_text("# t", encoding="utf-8")
    (diode / "blog" / "notes.txt").write_text("no", encoding="utf-8")
    names, truncated = blog.list_posts(str(diode))
    assert names == ["20260817_120000_000000", "20260817_110000_000000", "20260817_100000_000000"]
    assert truncated is False
    monkeypatch.setattr(blog, "POSTS_MAX", 2)
    names, truncated = blog.list_posts(str(diode))
    assert names == ["20260817_120000_000000", "20260817_110000_000000"]
    assert truncated is True


def test_list_posts_skips_symlinks_and_directories(tmp_path):
    diode = _blog(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("# secret", encoding="utf-8")
    (diode / "blog" / "20260817_120000_000000.md").symlink_to(outside)
    (diode / "blog" / "20260817_110000_000000.md").mkdir()
    (diode / "blog" / "20260817_100000_000000.md").write_text("# ok", encoding="utf-8")
    assert blog.list_posts(str(diode)) == (["20260817_100000_000000"], False)


def test_read_post_title_stamp_html_and_slug(tmp_path):
    diode = _blog(tmp_path)
    (diode / "blog" / "20260817_120000_000000.md").write_text(
        "# Hello *world*\n\nbody <x>\n", encoding="utf-8"
    )
    post = blog.read_post(str(diode), "20260817_120000_000000")
    assert post["name"] == "20260817_120000_000000"
    assert post["slug"] == "20260817_120000_000000"
    assert post["title"] == "Hello *world*"
    assert post["stamp"] == "2026-08-17 12:00:00 UTC"
    assert post["epoch"] == 1786968000.0
    assert '<h1 id="p20260817_120000_000000-h1">Hello <em>world</em></h1>' in post["html"]
    assert "<p>body &lt;x&gt;</p>" in post["html"]
    assert post["truncated"] is False


def test_slug_is_capped(tmp_path):
    diode = _blog(tmp_path)
    name = "a" * 200
    (diode / "blog" / f"{name}.md").write_text("# t", encoding="utf-8")
    post = blog.read_post(str(diode), name)
    assert len(post["slug"]) == blog.SLUG_MAX
    assert f'<h1 id="p{post["slug"]}-h1">' in post["html"]


def test_read_post_without_heading_uses_the_stamp_and_odd_names_are_slugged(tmp_path):
    diode = _blog(tmp_path)
    (diode / "blog" / "hello world!.md").write_text("just text", encoding="utf-8")
    post = blog.read_post(str(diode), "hello world!")
    assert post["title"] == "hello world!"
    assert post["stamp"] == "hello world!"
    assert post["epoch"] is None
    assert post["slug"].startswith("hello-world-")
    assert re.fullmatch(r"[A-Za-z0-9_-]+", post["slug"])


def test_names_that_sanitise_alike_get_distinct_slugs(tmp_path):
    """Element ids are built from the slug, so two names that differ only in
    punctuation, or only past the length cap, must not collide."""
    diode = _blog(tmp_path)
    for name in ("a b", "a-b", "x" * 70 + "1", "x" * 70 + "2"):
        (diode / "blog" / f"{name}.md").write_text("# t", encoding="utf-8")
    slugs = [
        blog.read_post(str(diode), n)["slug"]
        for n in ("a b", "a-b", "x" * 70 + "1", "x" * 70 + "2")
    ]
    assert len(set(slugs)) == 4
    assert all(len(s) <= blog.SLUG_MAX for s in slugs)


def test_read_post_caps_bytes_and_marks_truncation(tmp_path, monkeypatch):
    diode = _blog(tmp_path)
    monkeypatch.setattr(blog, "POST_READ_BYTES", 16)
    (diode / "blog" / "a.md").write_text("# " + "x" * 100, encoding="utf-8")
    post = blog.read_post(str(diode), "a")
    assert post["truncated"] is True
    assert post["title"] == "x" * 14


def test_read_post_refuses_links_and_missing(tmp_path):
    diode = _blog(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("# secret", encoding="utf-8")
    (diode / "blog" / "a.md").symlink_to(outside)
    assert blog.read_post(str(diode), "a") is None
    assert blog.read_post(str(diode), "missing") is None
    assert blog.read_post(str(diode), "../outside") is None


def test_read_post_with_an_undecodable_filename_still_renders(tmp_path):
    diode = _blog(tmp_path)
    name = os.fsdecode(b"\xff-post")
    (diode / "blog" / (name + ".md")).write_text("just text", encoding="utf-8")
    names, _ = blog.list_posts(str(diode))
    assert names == [name]
    post = blog.read_post(str(diode), name)
    for key in ("slug", "stamp", "title", "html"):
        post[key].encode("utf-8")
    assert post["title"] == "?-post"


@pytest.mark.parametrize(
    "total,page,expected",
    [
        (0, 1, (0, 0, 1)),
        (0, 2, None),
        (10, 1, (0, 10, 1)),
        (11, 1, (0, 10, 2)),
        (11, 2, (10, 11, 2)),
        (11, 3, None),
        (5, 0, None),
        (5, -1, None),
    ],
)
def test_paginate(total, page, expected):
    assert blog.paginate(total, page) == expected
