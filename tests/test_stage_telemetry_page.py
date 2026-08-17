"""String checks on the telemetry panel page: structure, copy discipline, and containment."""

import re

from stage import server, telemetry_page

HTML = telemetry_page.TELEMETRY_PAGE_HTML
STYLE = HTML[HTML.index("<style>") : HTML.index("</style>")]
BODY = HTML[HTML.index("<body>") : HTML.index("</body>")]
COPY = re.sub(r"<script>.*?</script>", "", BODY, flags=re.S)


def test_the_page_is_a_document_with_a_title_heading_main_and_skip_link():
    assert "<title>aurora — telemetry</title>" in HTML
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in HTML
    assert '<h1 id="wordmark">AURORA · TELEMETRY</h1>' in HTML
    assert '<a class="skip" href="#record">' in HTML
    assert "<main>" in HTML and "</main>" in HTML
    assert 'href="/"' in HTML  # the way back to the stream
    assert "scroll-padding-top: 64px" in STYLE


def test_the_sections_come_in_the_ruled_order_and_now_is_absent():
    ids = re.findall(r'<section id="([a-z-]+)"', HTML)
    assert ids == ["record", "story", "source", "diode", "streams"]
    assert 'id="now"' not in HTML
    assert "aria-live" not in HTML  # only role=status regions announce


def test_the_record_table_splits_measured_from_the_stages_reading():
    table = HTML[HTML.index('<table id="record-table"') : HTML.index("</table>")]
    assert table.count("<colgroup") == 2
    assert 'colspan="7" role="columnheader">Measured</th>' in table
    assert 'colspan="2" class="seam" role="columnheader">The stage\'s reading</th>' in table
    heads = re.findall(r'<th scope="col"[^>]*>(.*?)</th>', table)
    assert heads[1:9] == [
        "Ended",
        "Lived",
        "Turns",
        "Edits",
        "Errors",
        "Ending",
        "Verdict",
        "Noted",
    ]
    assert '<span aria-hidden="true">#</span><span class="vh">Incarnation</span>' in heads[0]
    assert '<caption class="vh">' in table
    assert 'role="table"' in table
    assert "MEASURED" in HTML and "THE STAGE'S READING" in HTML  # the card eyebrows


def test_the_order_control_is_one_labelled_select_with_the_five_orders_and_a_status():
    assert '<label for="order">Order by</label>' in HTML
    options = re.findall(r'<option value="([a-z]+)">([^<]+)</option>', HTML)
    assert options == [
        ("newest", "newest first"),
        ("lived", "longest lived"),
        ("turns", "most turns"),
        ("edits", "most self-edits"),
        ("verdict", "highest verdict"),
    ]
    assert '<div id="order-status" class="vh" role="status"></div>' in HTML
    assert 'id="noted-only"' in HTML and "only lives with a noted first" in HTML
    assert "aria-sort" not in HTML  # sorting is the select, not header buttons


def test_rows_expand_through_one_disclosure_button_with_aria_wiring():
    assert 'disc.setAttribute("aria-expanded", "false");' in HTML
    assert 'disc.setAttribute("aria-controls", cardTr.id);' in HTML
    assert '"Expand incarnation " + life.ordinal' in HTML
    assert '(open ? "Collapse" : "Expand")' in HTML
    assert 'cardTr.id = "life-" + life.ordinal + "-card";' in HTML
    assert "toggleRow" in HTML
    assert 'tr.addEventListener("click"' not in HTML  # no whole-row click


def test_polls_reconcile_rows_in_place_and_can_be_paused():
    assert "if (node !== cursor) body.insertBefore(node, cursor);" in HTML
    assert 'button id="pause" type="button" aria-pressed="false">pause updates</button>' in HTML
    assert "setInterval(fetchStream, 5000);" in HTML
    assert "setInterval(fetchLineage, 30000);" in HTML
    assert "if (paused) return;" in HTML
    assert "var ROW_LIMIT = 25;" in HTML
    assert 'id="show-all"' in HTML


def test_no_innerhtml_no_console_paths_no_query_state():
    script = "\n".join(re.findall(r"<script>(.*?)</script>", HTML, re.S))
    assert "innerHTML" not in script
    assert "outerHTML" not in script
    assert "insertAdjacentHTML" not in script
    assert "document.write" not in script
    for forbidden in (
        "/api/browse",
        "/api/file",
        "/download",
        "/api/diff",
        "8092",
        "x-console-token",
        "?token=",
        "console",
    ):
        assert forbidden not in HTML.lower(), forbidden
    assert "localStorage" not in HTML
    assert "document.cookie" not in HTML
    assert "location.search" not in HTML
    fetched = re.findall(r'fetch\("([^"]+)"', script)
    assert sorted(set(fetched)) == ["/api/lineage", "/api/stream"]
    assert '"/audio/" + encodeURIComponent(s.name)' in script


def test_the_copy_never_reads_as_a_scoreboard():
    lowered = COPY.lower()
    for word in ("leaderboard", "achievement", "score", "rank", "winner", "analyst key", "no key"):
        assert word not in lowered, word
    assert "leaderboard" not in HTML.lower()
    for cls_or_id in re.findall(r'(?:id|class)="([^"]+)"', HTML):
        assert "leaderboard" not in cls_or_id and "achievement" not in cls_or_id
    # Moments render in turn order, not by stars.
    assert "sort(function (a, b) { return a.turn - b.turn; })" in HTML
    assert "NOTED FIRST" in HTML
    assert '"— the stage"' in HTML
    assert "no reading of this life — the stage" in HTML
    assert "reading pending — the stage" in HTML
    assert "This page is read-only. Nothing here can reach the agent." in HTML


def test_no_type_falls_below_the_floor_and_body_is_sixteen():
    sizes = [int(n) for n in re.findall(r"font(?:-size)?:[^;{}]*?(\d+)px", STYLE)]
    assert sizes, "no px font sizes declared"
    assert min(sizes) >= 13, sorted(sizes)[:3]
    assert (
        "body { margin: 0; background: var(--ink-0); color: var(--paper); font: 400 16px/1.5"
        in STYLE
    )


def test_motion_and_targets_respect_reduced_motion_and_the_narrow_tier():
    assert "@media (prefers-reduced-motion: reduce)" in STYLE
    reduced = STYLE[STYLE.index("@media (prefers-reduced-motion: reduce)") :]
    assert "animation: none" in reduced
    assert "@media (max-width: 719px)" in STYLE
    narrow = STYLE[STYLE.index("@media (max-width: 719px)") :]
    assert "#record-table thead { display: none; }" in narrow
    assert ".disc { min-height: 44px; }" in narrow
    assert "min-height: 56px" in STYLE  # the strip
    assert "#to-stream { display: inline-flex; align-items: center; min-height: 44px" in STYLE
    # Nothing in the narrow tier lowers a target below 44 px.
    for rule in re.findall(r"[^{}]+\{[^}]*min-height:\s*(\d+)px[^}]*\}", narrow):
        assert int(rule) >= 44, narrow
    assert 'preload = "none"' in HTML
    assert 'a.setAttribute("aria-labelledby", text.id);' in HTML
    assert 'tabindex="0" aria-label="latest edit excerpt"' in HTML
    assert 'class="scroller" tabindex="0" aria-label="model lanes, scrollable"' in HTML


def test_the_offline_state_brightens_rather_than_dims():
    assert (
        "#strip.offline #state-word, #strip.offline #state-clock { color: var(--fault); }" in STYLE
    )
    assert (
        'id="offline" role="status" hidden>STAGE OFFLINE — this page cannot reach the stage' in HTML
    )


def test_the_route_is_wired_on_the_stream_handler():
    source = open(server.__file__, encoding="utf-8").read()
    handler = source[source.index("class StreamHandler") :]
    assert 'route == "/telemetry"' in handler
    assert "telemetry_page.TELEMETRY_PAGE_HTML" in handler


def test_pause_stops_the_clock_and_the_seam_and_units_are_structural():
    script = "\n".join(re.findall(r"<script>(.*?)</script>", HTML, re.S))
    assert "function tick() {\n  if (paused) return;" in script
    assert 'setClass($("strip"), "paused", paused);' in script
    assert "#strip.paused .dot { animation: none; }" in STYLE
    assert (
        "#strip.offline .dot { background: var(--fault); border-color: var(--fault); animation: none; }"
        in STYLE
    )
    assert "--seam:" in STYLE and "--control:" in STYLE
    assert (
        "#record-table tr.groups th.seam, #record-table .seam { border-left: 1px solid var(--seam); }"
        in STYLE
    )
    assert ".card .blk.reading { border-left: 1px solid var(--seam); padding-left: 24px; }" in STYLE
    base = STYLE[: STYLE.index("@media (max-width: 719px)")]
    assert "#record-table .unit { display: none; }" in base
    assert "#offline { flex: 1 1 100%;" in STYLE  # in flow, never over the record
    # Tool arguments never reach the page: only the phrased summary is shown.
    assert "e.summary || e.detail" not in script
    assert 'if (sub && !e.quoted && /^[\\[{]/.test(sub)) sub = "";' in script
