"""Markdown for the blog page: a deliberate subset, rendered with every character escaped.

The agent writes the posts, so the renderer is a containment surface: raw HTML in
the source never passes through, link destinations are allow-listed by scheme,
and images render as links so a viewer's browser never fetches an agent-chosen
URL. Anything the subset does not recognise renders as visible text.
"""

import html
import re

POST_READ_BYTES = 65_536
POSTS_MAX = 1000
POSTS_PER_PAGE = 10

# Every class excludes its own closer and is length-capped, so no opener rescans past the next closer.
_URL = r"[^\s()]{1,2048}(?:\([^\s()]{0,256}\)[^\s()]{0,2048})?"
_INLINE = re.compile(
    r"(?P<code>`{1,3})(?P<code_text>[^`]{1,4096}?)(?P=code)"
    r"|!\[(?P<img_alt>[^\[\]\n]{0,1000})\]\((?P<img_url>" + _URL + r")\)"
    r"|\[(?P<link_text>[^\[\]\n]{1,1000})\]\((?P<link_url>" + _URL + r")\)"
    r"|\*\*(?P<bold>[^\n]{1,4096}?)\*\*"
    r"|\*(?P<em>[^*\n]{1,4096}?)\*"
    r"|(?<![A-Za-z0-9_])_(?P<em2>[^_\n]{1,4096}?)_(?![A-Za-z0-9_])"
)


def safe_href(url):
    """The url when its scheme is http, https, mailto or an in-page anchor; else None."""
    url = (url or "").strip()
    lowered = url.lower()
    if lowered.startswith(("http://", "https://", "mailto:")) or url.startswith("#"):
        return url
    return None


def _anchor(href, inner):
    return f'<a href="{html.escape(href, quote=True)}" rel="noopener nofollow">{inner}</a>'


def render_inline(text):
    """Render inline markdown (code, bold, em, links, images-as-links) to escaped HTML."""
    out = []
    pos = 0
    for m in _INLINE.finditer(text):
        out.append(html.escape(text[pos : m.start()]))
        pos = m.end()
        if m.group("code") is not None:
            out.append(f"<code>{html.escape(m.group('code_text'))}</code>")
        elif m.group("img_alt") is not None:
            href = safe_href(m.group("img_url"))
            label = "image: " + html.escape(m.group("img_alt") or m.group("img_url"))
            out.append(_anchor(href, label) if href else html.escape(m.group(0)))
        elif m.group("link_text") is not None:
            href = safe_href(m.group("link_url"))
            inner = render_inline(m.group("link_text"))
            out.append(_anchor(href, inner) if href else html.escape(m.group(0)))
        elif m.group("bold") is not None:
            out.append(f"<strong>{render_inline(m.group('bold'))}</strong>")
        elif m.group("em") is not None:
            out.append(f"<em>{render_inline(m.group('em'))}</em>")
        else:
            out.append(f"<em>{render_inline(m.group('em2'))}</em>")
    out.append(html.escape(text[pos:]))
    return "".join(out)
