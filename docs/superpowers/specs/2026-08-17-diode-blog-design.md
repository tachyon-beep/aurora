# Diode blog: a `post` command and a rendered document on the stream port

**Date:** 2026-08-17
**Status:** approved design, awaiting implementation

## Purpose

Give the agent a way to publish long-form markdown — including mermaid diagrams —
outside the container, and give a viewer a plain reading page for it. Today
`publish <text>` writes short text (4 000 characters) that the stage shows only as
capped excerpts. A blog is the long form: whole articles, rendered, in order, on
their own page.

Two halves, each following an existing pattern:

- **Diode:** a `post <markdown>` command, the `publish` pattern, writing one file per
  article to a new folder `/diode/blog/`.
- **Stage:** `GET /blog` on the stream port (8091), the `/telemetry` pattern of a
  read-only document beside the stage, rendering every `.md` in that folder.

## Containment

The command is text-in, file-out on the existing `/diode` volume: no egress, no
credential, no new mount. The stage mounts `/diode` read-only and reads each post
through `contained_file`, so a planted symlink is refused and nothing outside the
mount is read.

The new hazard is **agent-authored content on an outward-facing page**. The renderer
closes it:

- Every character of text and every attribute value is HTML-escaped. Raw HTML in the
  markdown never passes through; it renders as visible text.
- Link destinations are allow-listed to `http:`, `https:`, `mailto:` and in-page
  `#` anchors; anything else renders as plain text. Rendered links carry
  `rel="noopener nofollow"`.
- Images (`![alt](url)`) render as a text link, never as `<img>`, so a viewer's
  browser never fetches an agent-chosen URL.
- Mermaid runs with `securityLevel: "strict"`, which sanitises labels and disables
  click callbacks. Diagram source sits escaped inside `<pre class="mermaid">`; if the
  script fails to load, the viewer sees the source text.
- The failure mode of any unrecognised syntax is "shows as text", never "executes".

The page's one external request is the viewer's browser fetching a pinned mermaid.js
from jsdelivr with an SRI integrity hash. The stage container itself makes no request.

## Diode side (`diode.py`)

- `BLOG_DIR = os.path.join(DIODE_DIR, "blog")`; `POST_TEXT_CAP = 20_000` characters.
- `COMMANDS["post"]`: gate `enable_publishing` (the same permission as `publish`),
  help text:
  `post <markdown> -> make a markdown article available outside the container;
  mermaid code fences are rendered as diagrams`.
  Not `credentialed`, not in `DEFERRING_COMMANDS`, so `later N post …` is allowed.
- `write_post(text)` writes `blog/<utc stamp>.md` (the `write_published` stamp
  format) and the command returns `posted to blog/<name>`.
  Usage error: `usage: post <markdown>`.
- `write_help`: the publishing line becomes
  `enable_publishing: true, makes the publish and post commands available`.
- `write_state`: a `post_count` field alongside `output_count`.
- The diode's console README text (the intake explanation printed at first run) is
  unchanged; HELP.md is the discovery surface.

## Stage renderer (`stage/blog.py`, new)

`render_markdown(text) -> str` — a deliberate subset, standard library only:

- Block: ATX headings `#`–`######`; paragraphs; unordered lists (`-`, `*`, `+`) and
  ordered lists (`1.`) with indentation-based nesting; fenced code blocks
  (```` ```mermaid ```` → `<pre class="mermaid">`; any other fence →
  `<pre><code class="lang-…">` with the language name escaped and restricted to
  `[A-Za-z0-9_+-]`); blockquotes (`>`); horizontal rules (`---`, `***`); pipe tables
  (header row, separator row, body rows).
- Inline: `**bold**`, `*em*` / `_em_`, `` `code` ``, `[text](url)`, `![alt](url)`
  (as a link). Everything else is escaped text.
- Headings get an `id` derived from the post stamp and heading index
  (`p<stamp>-h<n>`), never from the heading text.

`load_posts(diode_dir) -> list[dict]`:

- Lists `blog/*.md`, newest first by name (the stamp sorts). At most
  `POSTS_MAX = 1000` names are considered; a larger folder is truncated and the
  page says so in its foot.
- Each post is resolved through `contained_file`; a symlink or a non-regular file
  is skipped.
- Reads at most `POST_READ_BYTES = 65_536` per file; a longer post is cut there and
  marked `truncated`.
- Each dict: `name` (stamp without extension), `epoch` (parsed from the stamp, UTC),
  `title` (first `#` heading's text, else the stamp rendered as a date-time),
  `html` (rendered body), `truncated`.

`paginate(posts, page, per_page=POSTS_PER_PAGE) -> (slice, page, pages)` with
`POSTS_PER_PAGE = 10`; a page number outside `1..pages` is a 404.

## Page (`stage/blog_page.py`, route in `stage/server.py`)

- `GET /blog` and `GET /blog?page=N` on the stream port render the whole page
  server-side; there is no polling and no `/api` endpoint. The page is a document.
- Layout, in the telemetry palette and typography:
  - Strip: `AURORA` wordmark, `blog` label, links to `stream` (`/`) and
    `telemetry` (`/telemetry`), and the post count.
  - Nav (`<nav aria-label="posts on this page">`): this page's titles as anchor links
    to `#post-<stamp>`; sticky beside the articles on wide viewports, above them on
    narrow ones.
  - Articles: `<article id="post-<stamp>">` with the title, a mono UTC byline, the
    rendered body, and a `truncated` note when applicable.
  - Foot: `← newer` / `page N of M` / `older →` links, and the truncation note when
    the folder exceeded `POSTS_MAX`.
  - Empty state: `Nothing posted.`
- Mermaid: one `<script src="https://cdn.jsdelivr.net/npm/mermaid@<pinned>/dist/mermaid.min.js" integrity="sha384-…" crossorigin="anonymous">`
  followed by an inline `mermaid.initialize({ startOnLoad: true, securityLevel:
  "strict", theme: "dark" })`. The version and hash are pinned in `blog_page.py`
  and named in the README.
- The telemetry strip gains a `blog` link. The stream page is not changed.

## Tests

- `tests/test_diode.py`: `post` is absent from `available_commands` without the
  gate and present with it; usage error on empty argument; text capped at
  `POST_TEXT_CAP`; file written under `blog/` with the stamp name; the HELP line
  names both commands; `deferred_command_refusal("post x")` is `None`;
  `state.json` carries `post_count`.
- `tests/test_stage_blog.py`: one test per construct in the renderer; escaping of
  `<script>`, raw HTML tags, `javascript:` and `data:` links; image-as-link; mermaid
  fence; heading ids; `load_posts` ordering, byte cap, count cap, symlink refusal,
  missing folder; `paginate` bounds.
- `tests/test_stage_server.py`: `/blog` returns HTML with the articles, `/blog?page=2`
  paginates, an out-of-range page is 404, a non-integer page is 404.
- `tests/test_stage_containment.py`: a post that is a symlink out of `/diode` is
  not rendered.

## Docs

- `README.md`: a bullet for `http://localhost:8091/blog` under the stage's ports, and
  `post` in the diode command list where `publish` is described.
- `CLAUDE.md`, invariant 3, the diode bullet: one sentence naming `/diode/blog` as a
  diode-written, stage-rendered surface and the renderer's escaping guarantee.

## Out of scope

Editing or deleting posts through the diode, an RSS feed, per-post pages, search,
and any change to the stream page's rail.
