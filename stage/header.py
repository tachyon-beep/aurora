"""The masthead the document pages share with the stream page.

The stream page's header (stage/pages.py) is the reference: the wordmark, the
vertical rule, the two-row layout, the mono nav links and the repo line are
copied from it, and a test holds the copied rules byte-identical between the
two files. /telemetry and /blog build their headers here so the three pages
cannot drift apart; each passes its own name, right-hand cluster, nav links
and optional banner into the slots, and splices MASTHEAD_NARROW_CSS into its
own narrow media tier (the pages keep a single narrow tier each).

Unlike the stream page's masthead — a fixed band on an OBS canvas — this one
is sticky: the document pages scroll, and the nav links and any live cluster
stay reachable. Links keep a 44px touch target, taller than the stream page's
static row, because these pages are interactive documents.
"""

REPO = "github.com/tachyon-beep/aurora"

MASTHEAD_CSS = """
#masthead { position: sticky; top: 0; z-index: 20; display: grid;
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(36px, auto) minmax(26px, auto); row-gap: 6px; align-content: center;
  padding: 8px 20px; background: var(--ink-0); border-bottom: 1px solid var(--rule-2); }
#mh-a { display: flex; align-items: baseline; gap: 22px; min-width: 0; }
#wordmark { font: 600 26px/30px var(--sans); letter-spacing: .18em; color: var(--paper); }
.vrule { width: 1px; height: 20px; background: var(--rule-2); align-self: center; flex: none; }
#page-name { margin: 0; font: 600 13px/18px var(--mono); text-transform: uppercase;
  letter-spacing: .12em; color: var(--paper-dim); }
#mh-end { margin-left: auto; display: flex; align-items: center; gap: 14px; flex: none; }
#mh-b { display: flex; align-items: center; gap: 26px; min-width: 0; }
#mh-b a { display: inline-flex; align-items: center; min-height: 44px; padding: 0 6px;
  flex: none; white-space: nowrap;
  font: 400 13px/18px var(--mono); color: var(--paper-faint); text-decoration: none; }
#mh-b a:hover, #mh-b a:focus-visible { color: var(--vital); text-decoration: underline; }
#mh-fill { margin-left: auto; min-width: 0; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; font: 400 13px/18px var(--sans); color: var(--paper-faint); }
#repo { flex: none; font: 400 13px/18px var(--mono); color: var(--paper-faint); }
"""

MASTHEAD_NARROW_CSS = """
  #masthead { display: flex; flex-direction: column; align-items: stretch; gap: 6px;
    padding: 8px 12px; }
  #mh-a { flex-wrap: wrap; gap: 10px 14px; }
  #wordmark { font-size: 22px; line-height: 28px; letter-spacing: .14em; }
  .vrule { display: none; }
  #mh-b { flex-wrap: wrap; gap: 4px 14px; }
  #repo { display: none; }
"""


def masthead(page_name, cluster="", left="", fill="", right="", banner=""):
    """One document page's header, in the stream page's shape.

    Row A carries the wordmark, the page's name (its h1) and the page's
    right-hand cluster; row B the nav links, a flexible fill slot and the
    repo line. A banner (the telemetry page's offline notice) lands after
    row B, still inside the header.
    """
    return (
        '<header id="masthead">'
        '<div id="mh-a">'
        '<span id="wordmark">AURORA</span>'
        '<span class="vrule" aria-hidden="true"></span>'
        f'<h1 id="page-name">{page_name}</h1>'
        f'<div id="mh-end">{cluster}</div>'
        "</div>"
        '<div id="mh-b">'
        f'{left}<span id="mh-fill">{fill}</span><span id="repo">{REPO}</span>{right}'
        "</div>"
        f"{banner}"
        "</header>"
    )
