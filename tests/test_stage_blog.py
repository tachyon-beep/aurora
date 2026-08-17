import time

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


def test_crlf_and_blank_lines_are_tolerated():
    assert blog.render_markdown("a\r\n\r\n\r\nb\r\n") == "<p>a</p>\n<p>b</p>"
    assert blog.render_markdown("") == ""
    assert blog.render_markdown("\n\n") == ""


def test_first_heading():
    assert blog.first_heading("intro\n\n# The *title*\n\n## sub") == "The *title*"
    assert blog.first_heading("no heading") is None
    assert blog.first_heading("```\n# not a heading\n```\n# real") == "real"
