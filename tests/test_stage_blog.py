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
