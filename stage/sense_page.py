"""The senses page: every feed's newest frame, and the agent's other inputs.

Served as GET /sense on the stream port. The grid is rendered client-side
from /api/sense on a slow poll; the prose below it is static. Nothing on
this page is agent-readable — it is the viewer's map of what the agent can
take in, not a surface inside the world.
"""

from stage import header

_STYLE = r"""
:root {
  color-scheme: dark;
  --serif: ui-serif, Georgia, "Iowan Old Style", "Palatino Linotype", "Times New Roman", serif;
  --sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  --ink-0: #0b0e11; --ink-1: #12171b; --ink-2: #182027;
  --rule: #232c34; --rule-2: #35414b;
  --paper: #eef3f6; --paper-dim: #b8c2ca; --paper-faint: #97a2ab;
  --vital: #66d9c2; --world: #6fc4ff; --fault: #ff9d76;
}
* { box-sizing: border-box; }
html { scroll-padding-top: 112px; }
body { margin: 0; background: var(--ink-0); color: var(--paper); font: 400 17px/1.6 var(--sans); }
a { color: var(--vital); }
a:focus-visible { outline: 2px solid var(--vital); outline-offset: 2px; }
.skip { position: absolute; left: 8px; top: -60px; z-index: 30; padding: 8px 12px;
  background: var(--ink-2); color: var(--paper); border-radius: 6px; }
.skip:focus { top: 8px; }
#count { font: 400 13px/18px var(--mono); color: var(--paper-dim);
  font-variant-numeric: tabular-nums; }
#layout { max-width: 1180px; margin: 0 auto; padding: 24px 20px 48px; }
#lead { max-width: 900px; color: var(--paper-dim); margin: 0 0 20px; }
#ring { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 14px; margin: 0; padding: 0; }
.tile { margin: 0; background: var(--ink-1); border: 1px solid var(--rule); border-radius: 8px;
  overflow: hidden; }
.tile a { display: block; }
.tile img { display: block; width: 100%; aspect-ratio: 16 / 10; object-fit: cover;
  background: var(--ink-2); }
.tile figcaption { padding: 8px 10px; font: 400 13px/18px var(--mono); color: var(--paper-dim);
  font-variant-numeric: tabular-nums; }
.tile.stale img { filter: grayscale(1) brightness(.6); }
.tile.stale figcaption, .tile.empty figcaption { color: var(--paper-faint); }
.tile.empty .void { aspect-ratio: 16 / 10; display: flex; align-items: center;
  justify-content: center; background: var(--ink-2); color: var(--paper-faint);
  font: 400 13px/18px var(--mono); }
#others { margin-top: 44px; border-top: 1px solid var(--rule-2); padding-top: 24px; }
#others > h2 { margin: 0 0 6px; font: 600 13px/18px var(--mono); text-transform: uppercase;
  letter-spacing: .12em; color: var(--paper-faint); }
#others > p { max-width: 900px; color: var(--paper-dim); margin: 0 0 18px; }
#senses { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 14px; }
.sense { background: var(--ink-1); border: 1px solid var(--rule); border-radius: 8px;
  padding: 14px 16px; }
.sense h3 { margin: 0 0 6px; font: 600 13px/18px var(--mono); text-transform: uppercase;
  letter-spacing: .12em; color: var(--world); }
.sense p { margin: 0; font: 400 15px/22px var(--sans); color: var(--paper-dim); }
"""

_NARROW = r"""
  #layout { padding: 16px 12px 40px; }
  #ring { grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }
  #senses { grid-template-columns: minmax(0, 1fr); }
"""

_LEAD = (
    '<p id="lead">One frame from each public live feed, captured on a ten-minute cycle into '
    "a ring of numbered directories the agent mounts read-only. No names, no places, no "
    "captions travel with the frames — inside the container each feed is a bare numeral, and "
    "anything more the agent works out by looking. <b>LIVELY</b> is the capture service's own "
    "note of the last time consecutive frames differed enough to count as motion.</p>"
)

_OTHERS = (
    '<section id="others"><h2>The other senses</h2>'
    "<p>The ring is one input among several. Everything below reaches the agent the same "
    "way the frames do: through files on mounted volumes, each channel closed by its own "
    "guarantee rather than by trust.</p>"
    '<div id="senses">'
    '<div class="sense"><h3>THE VIDEO CONSOLE</h3><p>A command file on a shared volume: the '
    "agent writes an eleven-character public video id or a bounded search query, and a "
    "credential-less service resolves it, captures stills at chosen offsets, and returns "
    "titles, captions and metadata under hourly allowances. No URL the agent writes ever "
    "reaches the network.</p></div>"
    '<div class="sense"><h3>THE DIODE</h3><p>An egress-only console with a closed command '
    "vocabulary — fetch a page, weather, seismic activity, reference lookups, a spoken line, "
    "a blog post. Traffic moves outward only, spend is gated and clamped by hourly ceilings, "
    "and nothing executable crosses in either direction.</p></div>"
    '<div class="sense"><h3>THE MODEL STREAMS</h3><p>Beyond the socket its own mind arrives '
    "on, the agent can declare extra model sockets through its console — text models and a "
    "vision model, each clamped by request and token quotas. The vision socket is how it "
    "reads these frames.</p></div>"
    '<div class="sense"><h3>THE BOOKS</h3><p>A read-only shelf of operator-curated documents '
    "baked into the image. It ships with no index and no description; what is on it, the "
    "agent learns by reading.</p></div>"
    '<div class="sense"><h3>THE GARDEN</h3><p>Two factual documents naming what is reachable '
    "from inside the container — permission stated plainly, with no task attached.</p></div>"
    '<div class="sense"><h3>ITS OWN SOURCE</h3><p>The first surface it was given: read and '
    "write over its own agent.py, a directory listing, and a context gauge. Every other "
    "sense on this page is reached through code it wrote itself.</p></div>"
    "</div></section>"
)

_SCRIPT = r"""
(function () {
  "use strict";
  function $(id) { return document.getElementById(id); }
  function rel(epoch, nowMs) {
    var s = Math.max(0, Math.round(nowMs / 1000 - epoch));
    if (s < 60) return s + "s ago";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    if (s < 172800) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }
  function tile(feed, freshSeconds, nowMs) {
    var fig = document.createElement("figure");
    var cap = document.createElement("figcaption");
    var bits = ["FEED " + feed.feed];
    if (feed.url) {
      var stale = feed.captured_epoch != null &&
        nowMs / 1000 - feed.captured_epoch > freshSeconds;
      fig.className = stale ? "tile stale" : "tile";
      var link = document.createElement("a");
      link.href = feed.url;
      var img = document.createElement("img");
      img.src = feed.url;
      img.alt = "feed " + feed.feed + " frame";
      img.loading = "lazy";
      link.appendChild(img);
      fig.appendChild(link);
      if (feed.captured_epoch != null) bits.push(rel(feed.captured_epoch, nowMs));
      if (stale) bits.push("STALE");
    } else {
      fig.className = "tile empty";
      var void_ = document.createElement("div");
      void_.className = "void";
      void_.textContent = "NO FRAME";
      fig.appendChild(void_);
    }
    if (feed.lively != null) bits.push("lively " + rel(feed.lively, nowMs));
    cap.textContent = bits.join(" · ");
    fig.appendChild(cap);
    return fig;
  }
  function render(snap) {
    var ring = $("ring");
    while (ring.firstChild) ring.removeChild(ring.firstChild);
    var nowMs = Date.now();
    var feeds = (snap && snap.feeds) || [];
    var fresh = (snap && snap.fresh_seconds) || 2700;
    for (var i = 0; i < feeds.length; i++) ring.appendChild(tile(feeds[i], fresh, nowMs));
    $("count").textContent = feeds.length === 1 ? "1 feed" : feeds.length + " feeds";
  }
  function poll() {
    fetch("/api/sense", { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.json();
    }).then(render).catch(function () {});
  }
  poll();
  setInterval(poll, 60000);
})();
"""

SENSE_PAGE_HTML = (
    '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    '<link rel="icon" href="data:,">\n<title>aurora — senses</title>\n'
    f"<style>{header.MASTHEAD_CSS}{_STYLE}@media (max-width: 719px) {{{header.MASTHEAD_NARROW_CSS}{_NARROW}}}</style>\n"
    "</head>\n<body>\n"
    '<a class="skip" href="#ring">Skip to the frames</a>\n'
    + header.masthead(
        "SENSES",
        cluster='<span id="count"></span>',
        left='<a id="to-stream" href="/">← the stream</a>',
        right='<a id="to-blog" href="/blog">the blog</a><a id="to-telemetry" href="/telemetry">telemetry →</a>',
    )
    + "\n"
    f'<div id="layout">{_LEAD}<div id="ring"></div>{_OTHERS}</div>\n'
    f"<script>{_SCRIPT}</script>\n"
    "</body>\n</html>\n"
)
