"""The blog: a server-rendered document of the diode's posts on the stream port.

Served as GET /blog[?page=N]. Every string that came from a post is HTML-escaped
before it reaches this template, or comes from blog.render_markdown, which
escapes as it renders. The one script is mermaid, pinned by version and
integrity hash; without it the diagrams show as their source text.
"""

import html

MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@11.16.1/dist/mermaid.min.js"
MERMAID_INTEGRITY = "sha384-aBQXj4hK6Jm05i7aQAsUV3bLdSUrHX1BGYfMB0166TtWt/RRaw+h0Eelme9OCOvy"

_STYLE = r"""
:root {
  color-scheme: dark;
  --serif: ui-serif, Georgia, "Iowan Old Style", "Palatino Linotype", "Times New Roman", serif;
  --sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  --ink-0: #0b0e11; --ink-1: #12171b; --ink-2: #182027;
  --rule: #232c34; --rule-2: #35414b;
  --paper: #eef3f6; --paper-dim: #b8c2ca; --paper-faint: #97a2ab;
  --vital: #66d9c2; --world: #6fc4ff;
}
* { box-sizing: border-box; }
html { scroll-padding-top: 72px; }
body { margin: 0; background: var(--ink-0); color: var(--paper); font: 400 17px/1.6 var(--sans); }
a { color: var(--vital); }
a:focus-visible { outline: 2px solid var(--vital); outline-offset: 2px; }
.skip { position: absolute; left: 8px; top: -60px; z-index: 30; padding: 8px 12px;
  background: var(--ink-2); color: var(--paper); border-radius: 6px; }
.skip:focus { top: 8px; }
#strip { position: sticky; top: 0; z-index: 20; min-height: 56px; display: flex; align-items: center;
  flex-wrap: wrap; gap: 6px 18px; padding: 6px 20px; background: var(--ink-1);
  border-bottom: 1px solid var(--rule-2); }
#wordmark { margin: 0; font: 600 16px/24px var(--sans); letter-spacing: .18em; white-space: nowrap; }
#strip a { display: inline-flex; align-items: center; min-height: 44px; padding: 0 6px;
  font: 500 15px/20px var(--sans); text-decoration: none; }
#strip a:hover { text-decoration: underline; }
#count { margin-left: auto; font: 400 14px/20px var(--mono); color: var(--paper-dim);
  font-variant-numeric: tabular-nums; }
#layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 24px;
  max-width: 1180px; margin: 0 auto; padding: 24px 20px 48px; }
@media (min-width: 900px) { #layout { grid-template-columns: 240px minmax(0, 1fr); } }
#posts-nav { align-self: start; position: sticky; top: 72px; }
#posts-nav h2 { margin: 0 0 8px; font: 600 13px/18px var(--mono); text-transform: uppercase;
  letter-spacing: .12em; color: var(--paper-faint); }
#posts-nav ol { margin: 0; padding: 0; list-style: none; }
#posts-nav li { margin: 0 0 6px; }
#posts-nav a { display: block; padding: 6px 8px; border-radius: 6px; text-decoration: none;
  color: var(--paper-dim); font: 400 15px/20px var(--sans); }
#posts-nav a:hover { background: var(--ink-1); color: var(--paper); }
main { min-width: 0; }
article { padding: 24px 0 32px; border-bottom: 1px solid var(--rule); }
article:last-of-type { border-bottom: 0; }
article header .byline { font: 400 13px/18px var(--mono); color: var(--paper-faint);
  font-variant-numeric: tabular-nums; }
article h1 { margin: 6px 0 16px; font: 600 30px/1.2 var(--serif); }
article h2 { margin: 28px 0 10px; font: 600 23px/1.25 var(--serif); }
article h3 { margin: 22px 0 8px; font: 600 19px/1.3 var(--sans); }
article h4, article h5, article h6 { margin: 18px 0 6px; font: 600 16px/1.3 var(--sans); }
article p, article ul, article ol, article blockquote, article table { margin: 0 0 14px; }
article ul, article ol { padding-left: 24px; }
article li > ul, article li > ol { margin: 4px 0 0; }
article blockquote { margin-left: 0; padding: 4px 16px; border-left: 3px solid var(--rule-2);
  color: var(--paper-dim); }
article code { font: 400 .92em var(--mono); background: var(--ink-2); padding: 1px 5px;
  border-radius: 4px; }
article pre { margin: 0 0 16px; padding: 12px 14px; overflow-x: auto; background: var(--ink-1);
  border: 1px solid var(--rule); border-radius: 8px; }
article pre code { background: none; padding: 0; font-size: 14px; line-height: 1.5; }
article pre.mermaid { background: var(--ink-1); text-align: center; white-space: pre-wrap;
  font: 400 13px/1.5 var(--mono); color: var(--paper-dim); }
article pre.mermaid svg { max-width: 100%; height: auto; }
article table { border-collapse: collapse; display: block; overflow-x: auto; }
article th, article td { padding: 6px 10px; border: 1px solid var(--rule); text-align: left;
  vertical-align: top; }
article th { background: var(--ink-1); font-weight: 600; }
article hr { border: 0; border-top: 1px solid var(--rule-2); margin: 24px 0; }
article a { word-break: break-word; }
.note { font: 400 14px/20px var(--mono); color: var(--paper-faint); }
#foot { display: flex; align-items: center; justify-content: space-between; gap: 16px;
  padding: 24px 0 0; border-top: 1px solid var(--rule-2); font: 400 15px/20px var(--sans);
  color: var(--paper-dim); }
#foot a { min-height: 44px; display: inline-flex; align-items: center; padding: 0 6px; }
#foot .gap { visibility: hidden; }
.empty { padding: 48px 0; color: var(--paper-faint); font: 400 17px/1.5 var(--serif); }
"""


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _nav(posts):
    if not posts:
        return ""
    items = "".join(
        f'<li><a href="#post-{html.escape(p["slug"], quote=True)}">{html.escape(p["title"])}</a></li>'
        for p in posts
    )
    return (
        '<nav id="posts-nav" aria-label="posts on this page"><h2>On this page</h2>'
        f"<ol>{items}</ol></nav>"
    )


def _article(post):
    slug = html.escape(post["slug"], quote=True)
    note = ""
    if post.get("truncated"):
        note = '<p class="note">This post was cut at the page\'s reading limit.</p>'
    return (
        f'<article id="post-{slug}"><header><p class="byline">{html.escape(post["stamp"])}</p>'
        f"</header>{post['html']}{note}</article>"
    )


def _foot(page, pages, list_truncated):
    newer = (
        f'<a href="/blog?page={page - 1}">← newer</a>' if page > 1 else '<span class="gap">·</span>'
    )
    older = (
        f'<a href="/blog?page={page + 1}">older →</a>'
        if page < pages
        else '<span class="gap">·</span>'
    )
    parts = [f'<div id="foot">{newer}<span>page {page} of {pages}</span>{older}</div>']
    if list_truncated:
        parts.append(
            '<p class="note">The folder holds more than this page can list; older posts are not listed.</p>'
        )
    return "".join(parts)


def render_page(posts, page, pages, total, list_truncated):
    """The whole blog page for one slice of posts (already read and rendered)."""
    body = "".join(_article(p) for p in posts) if posts else '<p class="empty">Nothing posted.</p>'
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<link rel="icon" href="data:,">\n<title>aurora — blog</title>\n'
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        '<a class="skip" href="#posts">Skip to the posts</a>\n'
        '<header id="strip"><h1 id="wordmark">AURORA · BLOG</h1>'
        '<a href="/">← the stream</a><a href="/telemetry">telemetry</a>'
        f'<span id="count">{_plural(total, "post")}</span></header>\n'
        f'<div id="layout">{_nav(posts)}<main id="posts">{body}{_foot(page, pages, list_truncated)}</main></div>\n'
        f'<script src="{MERMAID_URL}" integrity="{MERMAID_INTEGRITY}" crossorigin="anonymous"></script>\n'
        "<script>\nif (window.mermaid) { mermaid.initialize({ startOnLoad: true, "
        'securityLevel: "strict", theme: "dark" }); }\n</script>\n'
        "</body>\n</html>\n"
    )
