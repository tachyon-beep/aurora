"""Markdown for the blog page: a deliberate subset, rendered with every character escaped.

The agent writes the posts, so the renderer is a containment surface: raw HTML in
the source never passes through, link destinations are allow-listed by scheme,
and images render as links so a viewer's browser never fetches an agent-chosen
URL. Anything the subset does not recognise renders as visible text.
"""

import datetime
import hashlib
import html
import os
import re

from stage.data import contained_file

POST_READ_BYTES = 65_536
POSTS_MAX = 1000
POSTS_PER_PAGE = 10
SLUG_MAX = 64

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


_FENCE_OPEN = re.compile(r"\A {0,3}(`{3,}|~{3,})[ \t]*([^\s`]*).*\Z")
_HEADING = re.compile(r"\A {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*\Z")
_HR = re.compile(r"\A {0,3}(?:-[ \t]*){3,}\Z|\A {0,3}(?:\*[ \t]*){3,}\Z|\A {0,3}(?:_[ \t]*){3,}\Z")
_LIST_ITEM = re.compile(r"\A( *)(?:([-*+])|(\d{1,9})[.)])[ \t]+(.*)\Z")
_QUOTE = re.compile(r"\A {0,3}>[ ]?")
_TABLE_SEP = re.compile(r"\A\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*\Z")
_LANG = re.compile(r"\A[A-Za-z0-9_+-]{1,32}\Z")
MAX_NESTING = 16


def _fence_closes(line, fence):
    stripped = line.strip()
    return stripped.startswith(fence[0] * len(fence)) and set(stripped) == {fence[0]}


def _is_block_start(line):
    return bool(
        _FENCE_OPEN.match(line)
        or _HEADING.match(line)
        or _HR.match(line)
        or _QUOTE.match(line)
        or _LIST_ITEM.match(line)
    )


def _split_row(line):
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", row)]


def _table_at(lines, i):
    """True when lines[i] is a table header row and lines[i + 1] its delimiter row.

    The delimiter row must carry as many cells as the header, as GFM requires.
    Without that a line of dashes under any prose holding a pipe reads as a
    table, and since a table now interrupts a paragraph that is reachable from
    ordinary text rather than only from a block start.
    """
    if "|" not in lines[i] or i + 1 >= len(lines):
        return False
    separator = lines[i + 1]
    if not _TABLE_SEP.match(separator) or "-" not in separator:
        return False
    return len(_split_row(separator)) == len(_split_row(lines[i]))


def _render_table(lines):
    header = _split_row(lines[0])
    body = [_split_row(line) for line in lines[2:]]
    head = "".join(f"<th>{render_inline(c)}</th>" for c in header)
    rows = []
    for cells in body:
        cells = cells[: len(header)]
        rows.append("<tr>" + "".join(f"<td>{render_inline(c)}</td>" for c in cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _dedent(lines):
    indents = [len(line) - len(line.lstrip(" ")) for line in lines if line.strip()]
    cut = min(indents) if indents else 0
    return [line[cut:] if line.strip() else "" for line in lines]


def _render_list(lines, id_prefix, counter, depth=0):
    first = _LIST_ITEM.match(lines[0])
    base = len(first.group(1))
    ordered = first.group(3) is not None
    items = []
    for line in lines:
        m = _LIST_ITEM.match(line)
        if m and len(m.group(1)) <= base:
            items.append([m.group(4), []])
        elif items:
            items[-1][1].append(line)
    parts = []
    for text, children in items:
        inner = render_inline(text)
        if any(c.strip() for c in children):
            if depth >= MAX_NESTING:
                inner += render_inline(chr(10).join(_dedent(children)))
            else:
                inner += _render_blocks(_dedent(children), id_prefix, counter, depth + 1)
        parts.append(f"<li>{inner}</li>")
    if ordered:
        start = int(first.group(3))
        attr = f' start="{start}"' if start != 1 else ""
        return f"<ol{attr}>{''.join(parts)}</ol>"
    return f"<ul>{''.join(parts)}</ul>"


def _list_block(lines, i):
    """(the lines of the list starting at i, index after it).

    The block runs while lines are items or indented continuations. A blank line
    stays inside only when an item or continuation follows it. An item at the
    outer indent whose marker kind differs from the first (ordered vs bullet)
    starts a new list, so it ends this block.
    """
    first = _LIST_ITEM.match(lines[i])
    base = len(first.group(1))
    ordered = first.group(3) is not None
    n = len(lines)

    def _same_list(line):
        m = _LIST_ITEM.match(line)
        if m and len(m.group(1)) <= base:
            return (m.group(3) is not None) == ordered
        return bool(m) or line.startswith(" ")

    block = [lines[i]]
    i += 1
    while i < n:
        nxt = lines[i]
        if not nxt.strip():
            follows = lines[i + 1] if i + 1 < n else ""
            if follows.strip() and _same_list(follows):
                block.append(nxt)
                i += 1
                continue
            break
        if _same_list(nxt):
            block.append(nxt)
            i += 1
            continue
        break
    return block, i


def _render_blocks(lines, id_prefix, counter, depth=0):
    out = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = _FENCE_OPEN.match(line)
        if m:
            fence, lang = m.group(1), m.group(2)
            body = []
            i += 1
            while i < n and not _fence_closes(lines[i], fence):
                body.append(lines[i])
                i += 1
            i += 1
            code = html.escape("\n".join(body))
            if lang.lower() == "mermaid":
                out.append(f'<pre class="mermaid">{code}</pre>')
            else:
                cls = f' class="lang-{lang}"' if _LANG.match(lang) else ""
                out.append(f"<pre><code{cls}>{code}</code></pre>")
            continue
        m = _HEADING.match(line)
        if m:
            counter[0] += 1
            level = len(m.group(1))
            hid = f' id="{html.escape(id_prefix, quote=True)}h{counter[0]}"' if id_prefix else ""
            out.append(f"<h{level}{hid}>{render_inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if _HR.match(line):
            out.append("<hr>")
            i += 1
            continue
        if _QUOTE.match(line):
            quoted = []
            while i < n and _QUOTE.match(lines[i]):
                quoted.append(_QUOTE.sub("", lines[i], count=1))
                i += 1
            if depth >= MAX_NESTING:
                out.append(f"<blockquote><p>{render_inline(chr(10).join(quoted))}</p></blockquote>")
            else:
                out.append(
                    f"<blockquote>{_render_blocks(quoted, id_prefix, counter, depth + 1)}</blockquote>"
                )
            continue
        if _table_at(lines, i):
            table = [lines[i], lines[i + 1]]
            i += 2
            while i < n and lines[i].strip() and "|" in lines[i]:
                table.append(lines[i])
                i += 1
            out.append(_render_table(table))
            continue
        if _LIST_ITEM.match(line):
            block, i = _list_block(lines, i)
            out.append(_render_list(block, id_prefix, counter, depth))
            continue
        para = [line]
        i += 1
        while (
            i < n and lines[i].strip() and not _is_block_start(lines[i]) and not _table_at(lines, i)
        ):
            para.append(lines[i])
            i += 1
        out.append(f"<p>{render_inline(chr(10).join(para))}</p>")
    return "\n".join(out)


def _lines(text):
    """text split into lines, with CRLF and lone CR normalised to LF first."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def render_markdown(text, id_prefix=""):
    """Render a markdown document to escaped HTML.

    id_prefix, when given, is prepended to each heading's id ("<prefix>h<n>", n
    counting from 1 in document order) so anchors are stable and never derived
    from heading text.
    """
    return _render_blocks(_lines(text), id_prefix, [0])


def first_heading(text):
    """The text of the first ATX heading outside a code fence, or None."""
    fence = None
    for line in _lines(text):
        if fence is not None:
            if _fence_closes(line, fence):
                fence = None
            continue
        m = _FENCE_OPEN.match(line)
        if m:
            fence = m.group(1)
            continue
        m = _HEADING.match(line)
        if m:
            return m.group(2)
    return None


_STAMP_FORMAT = "%Y%m%d_%H%M%S_%f"


def _stamp_epoch(name):
    try:
        parsed = datetime.datetime.strptime(name, _STAMP_FORMAT)
    except ValueError:
        return None
    return parsed.replace(tzinfo=datetime.timezone.utc).timestamp()


def _stamp_label(name, epoch):
    if epoch is None:
        return name
    when = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    return when.strftime("%Y-%m-%d %H:%M:%S UTC")


def _slug(name):
    """A bounded id-safe stem for name, distinct for names sanitising to the same text.

    A stem the sanitiser leaves untouched is used as it stands. Any other stem is
    truncated to leave room for a digest of the full name, so two files whose
    names differ only in punctuation or past the cap do not share an element id.
    The digest is taken over surrogatepass bytes: a name carrying the surrogates
    os.listdir produces for undecodable bytes would otherwise digest to the same
    replacement characters as a name that holds those characters literally.
    """
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", name)[:SLUG_MAX]
    if cleaned == name:
        return cleaned
    digest = hashlib.sha256(name.encode("utf-8", "surrogatepass")).hexdigest()[:8]
    return f"{cleaned[: SLUG_MAX - len(digest) - 1]}-{digest}"


def list_posts(diode_dir):
    """(post stems newest first, truncated) for the regular .md files under blog/."""
    blog_dir = os.path.join(diode_dir, "blog")
    try:
        names = sorted((n for n in os.listdir(blog_dir) if n.endswith(".md")), reverse=True)
    except OSError:
        return [], False
    truncated = len(names) > POSTS_MAX
    kept = []
    for name in names[:POSTS_MAX]:
        if contained_file(diode_dir, os.path.join(blog_dir, name)) is not None:
            kept.append(name[: -len(".md")])
    return kept, truncated


def read_post(diode_dir, name):
    """One rendered post by stem, or None when it is missing, a link, or not a regular file."""
    blog_dir = os.path.join(diode_dir, "blog")
    if "/" in name or name in ("", ".", ".."):
        return None
    full = contained_file(diode_dir, os.path.join(blog_dir, name + ".md"))
    if full is None:
        return None
    try:
        with open(full, "rb") as f:
            raw = f.read(POST_READ_BYTES + 1)
    except OSError:
        return None
    truncated = len(raw) > POST_READ_BYTES
    text = raw[:POST_READ_BYTES].decode("utf-8", "replace")
    epoch = _stamp_epoch(name)
    slug = _slug(name)
    label = name.encode("utf-8", "replace").decode("utf-8")
    return {
        "name": name,
        "slug": slug,
        "epoch": epoch,
        "stamp": _stamp_label(label, epoch),
        "title": first_heading(text) or _stamp_label(label, epoch),
        "html": render_markdown(text, id_prefix=f"p{slug}-"),
        "truncated": truncated,
    }


def paginate(total, page, per_page=POSTS_PER_PAGE):
    """(start, end, pages) for a 1-based page over total items, or None when out of range."""
    pages = max(1, -(-total // per_page))
    if not isinstance(page, int) or page < 1 or page > pages:
        return None
    start = (page - 1) * per_page
    return start, min(total, start + per_page), pages
