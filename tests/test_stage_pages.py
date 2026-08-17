import re

from stage import commentary, pages

HTML = pages.STREAM_PAGE_HTML


def test_the_commentary_never_borrows_the_subjects_registers():
    """The commentator is a third register. Mistaking it for the subject is the one
    thing this block must never do, so the guard reads sentinels rather than trying
    to infer where the CSS ends."""
    start = HTML.index("/* commentary:start */")
    end = HTML.index("/* commentary:end */")
    block = HTML[start:end]
    assert "#now-colour" in block, "the sentinels do not span the whole block"
    assert "#now-evidence" in block, "the sentinels do not span the whole block"
    for token in ("--think", "--say", "--act", "--serif"):
        assert token not in block, token


def test_the_now_panel_carries_no_byline():
    """Operator ruling 2026-08-17: the NOW box shows the line and its evidence only.
    The masthead rotation still suffixes the colour line with "— the stage" when it
    carries it, so the generated register stays attributed where it travels."""
    now = HTML[
        HTML.index('<section id="now"') : HTML.index("</section>", HTML.index('<section id="now"'))
    ]
    assert "the stage, not the subject" not in now
    assert 'id="now-by"' not in HTML and 'id="now-dot"' not in HTML
    assert '" — the stage"' in HTML


def test_the_page_never_writes_commentary_with_inner_html():
    for line in HTML.split("\n"):
        if "innerHTML" in line:
            assert "commentary" not in line and "colour" not in line and "play" not in line


def test_stream_page_has_an_audio_element_and_caption():
    assert 'id="speak-audio"' in HTML
    assert 'id="speak-caption"' in HTML


def test_the_caption_sits_above_the_foot_so_the_panel_does_not_clip_it():
    """`#reached` is a flex column inside a 136px ribbon row and `#reached-foot` carries
    `margin-top: auto`, so anything after the foot is pushed past the panel's
    `overflow: hidden` edge and never reads. The caption must render whether or not
    playback succeeds, so its position is behaviour, not decoration."""
    assert HTML.index('id="said-text"') < HTML.index('id="speak-caption"')
    assert HTML.index('id="speak-caption"') < HTML.index('id="reached-foot"')


def test_the_sound_button_adds_no_height_to_the_panels_flow():
    """Measured in a real browser at 1920x1080, in the `.spoke.is-captioned`
    state: with `#sound-on` stacked in `#reached-said`'s block flow (margin-top:
    6px), revealing it grew `#reached-said` from 67px to 97px and pushed
    `#reached-foot` from 1030-1048 to 1060-1078 -- entirely past the panel's
    padding-box bottom (1055, itself 7px below the foot's own resting bottom
    edge of 1048), where `overflow: hidden` clips it. Anchoring the button
    with `position: absolute` against `#reached` instead costs zero flow
    height: the foot measured at 1030-1048 in both button states, with the
    button (left 1723.9, right 1873) never reaching as far left as the
    foot's rendered text (right edge 1695.8-1711.4 across the panel's actual
    foot strings), so nothing overlaps."""
    sound_on = HTML[HTML.index("\n#sound-on {") :]
    sound_on = sound_on[: sound_on.index("}")]
    assert "position: absolute" in sound_on
    assert "margin-top" not in sound_on
    assert "align-self" not in sound_on
    assert "flex: none" not in sound_on

    reached = HTML[HTML.index("\n#reached {") :]
    reached = reached[: reached.index("}")]
    assert "position: relative" in reached

    reached_foot = HTML[HTML.index("\n#reached-foot {") :]
    reached_foot = reached_foot[: reached_foot.index("}")]
    assert "padding-right" in reached_foot


def test_the_caption_clamps_the_published_line_to_make_room():
    assert "#reached.is-captioned #said-text" in HTML
    assert 'setClass($("reached"), "is-captioned"' in HTML


def test_the_caption_replaces_the_placeholder_that_would_contradict_it():
    """`renderRibbon` leaves `#said-text` empty whenever nothing has been published,
    and marks a publication with `.spoke`. Speech is not publication, so an utterance
    before the first publish would leave an empty `#said-text` sitting directly above
    a caption of what was just said. `#said-text` is hidden in that state and the
    caption takes the panel's line."""
    assert "#reached.is-captioned:not(.spoke) #said-text { display: none; }" in HTML
    assert "#reached.is-captioned:not(.spoke) #speak-caption" in HTML


def test_the_panel_accent_lights_for_an_utterance_as_well_as_a_publication():
    assert "#reached.is-captioned { border-left: 2px solid var(--say); }" in HTML


def test_the_outward_panels_are_one_panel():
    assert 'id="reached"' in HTML
    assert 'id="asked"' not in HTML
    assert 'id="said"' not in HTML


def test_the_merged_panel_keeps_the_whole_playback_path():
    """The merge must not cost the audio path: Task 7's control lives here too."""
    for token in ('id="speak-audio"', 'id="speak-caption"', 'id="sound-on"', "renderSpoken"):
        assert token in HTML, token


def test_the_merged_panel_states_the_fact_across_lives_not_per_life():
    assert "has never reached outside the box" in HTML


def test_the_merged_panel_playback_path_is_structurally_wired():
    """No live stack here to drop a file into `spoken/` and watch it play, so this
    checks the structural equivalent: `#speak-audio` is nested inside the `#reached`
    section (not a sibling), and `renderSpoken` is still wired into `render`."""
    start = HTML.index('id="reached"')
    end = HTML.index("</section>", start)
    section = HTML[start:end]
    assert 'id="speak-audio"' in section
    assert "\n  renderSpoken();\n" in HTML


def test_the_reached_said_wrapper_does_not_shrink_below_its_content():
    """`#said-stamp`, `#said-text` and `#speak-caption` carry a load-bearing
    `flex: none` (they used to be direct flex children of `.panel`, a flex column).
    The `#reached-said` wrapper introduced by the merge now sits between them and
    `.panel`, so it is the actual flex item; without its own `flex: none` it would
    take the default `flex-shrink: 1` and could be compressed below its content
    height inside the fixed-height ribbon row, clipping the caption stack."""
    assert "#reached-said { flex: none; }" in HTML


def test_stream_page_plays_each_utterance_once_and_only_when_fresh():
    assert "spokenPlayed" in HTML
    assert "/audio/" in HTML
    assert "renderSpoken(" in HTML


def test_render_calls_render_spoken():
    # Pins the call site, not just the function definition: deleting the call
    # from render would leave every other assertion here green even though
    # renderSpoken would never run.
    assert "\n  renderSpoken();\n" in HTML


def test_playback_queues_utterances_instead_of_playing_only_the_newest():
    # The diode runs a whole command batch in one cycle, so one snapshot can
    # carry two utterances. Reading only sp[0] drops the older one for good, and
    # reassigning src cuts off one still speaking.
    assert "spokenQueue" in HTML
    assert "for (var i = sp.length - 1; i >= 0; i--)" in HTML
    assert "spokenQueue.push({ name: name, text:" in HTML
    assert "if (!a || spokenBusy || !spokenQueue.length) return;" in HTML


def test_the_queue_advances_on_terminal_playback_but_holds_autoplay_refusal():
    # An unhandled terminal failure would leave spokenBusy set and wedge the
    # queue. NotAllowedError is recoverable and must return before the advance;
    # both branches are executed, not just grepped, in test_stage_pages_js.py.
    assert 'a.addEventListener("ended", spokenAdvance)' in HTML
    assert 'a.addEventListener("error", spokenAdvance)' in HTML
    assert 'if (e && e.name === "NotAllowedError") {' in HTML
    assert "if (mine !== spokenCurrent) return;" in HTML
    assert "spokenAdvance();" in HTML


def test_the_played_set_survives_a_reload():
    # In memory only, an OBS scene switch with "refresh browser when scene
    # becomes active" replays the last utterance inside the freshness window.
    assert 'window.localStorage.getItem("spokenPlayed")' in HTML
    assert 'window.localStorage.setItem("spokenPlayed"' in HTML
    assert "SPOKEN_MEMORY" in HTML


def test_a_future_dated_utterance_never_plays():
    # The agent can write into /diode/spoken, and a stamp in the future would
    # otherwise stay inside the freshness window forever.
    assert "if (ageMs < 0 || ageMs > 180000) {" in HTML
    assert "markSpokenPlayed(name);" in HTML


def test_the_playback_freshness_gate_matches_the_commentary_recency_window():
    """The page plays only what the commentator would still call recent. Deriving the
    bound from `commentary.RECENT_SECONDS` keeps the two from drifting apart, the way
    `test_silence_threshold_matches_the_pages_state_ladder` pins the state ladder."""
    match = re.search(r"ageMs > (\d+)", HTML)
    assert match, "the playback freshness gate moved or was renamed"
    assert int(match.group(1)) == commentary.RECENT_SECONDS * 1000


def test_the_streams_have_their_own_panel_not_a_masthead_row():
    """#lanes was a flex row of one chip per declared stream in a fixed-width
    masthead. The stream count is agent-controlled: at six it crushed the legend."""
    assert 'id="lanes"' not in HTML
    assert 'id="streams"' in HTML
    assert 'id="stream-rows"' in HTML
    assert "renderLanes" in HTML
    assert "snap.lanes" in HTML
    assert "tokens_hour" in HTML


def test_the_streams_panel_says_which_one_the_harness_gave_it():
    """core is the socket the agent was born with; every other stream it declared.
    That distinction is the entire point of the panel."""
    assert "GIVEN" in HTML
    assert "BUILT" in HTML


def test_the_legend_no_longer_shares_a_row_with_the_streams():
    mh_b = HTML[HTML.index('id="mh-b"') : HTML.index('id="death-sweep"')]
    assert "c-think" in mh_b and "c-say" in mh_b and "c-act" in mh_b
    assert "lane" not in mh_b


def test_the_lane_slice_matches_what_the_grid_can_show():
    """#stream-rows fits 3 rows x 2 columns = 6 lanes without clipping; a larger
    slice would render lanes into overflow that #stream-foot never discloses."""
    assert "lanes.slice(0, 6)" in HTML


def test_the_ribbon_gives_the_streams_the_widest_column():
    block = HTML[HTML.index("#ribbon {") :]
    block = block[: block.index("}")]
    assert "grid-template-columns: 1fr 1.6fr 1fr" in block, block


def _clamp_lines(selector):
    """The -webkit-line-clamp declared for one selector in the stream page CSS.
    Anchored on a newline so `.clamp.think` finds the rule and not
    `.turn.wake .clamp.think`."""
    start = HTML.index("\n" + selector + " {")
    block = HTML[start : HTML.index("}", start)]
    match = re.search(r"-webkit-line-clamp:\s*(\d+)", block)
    assert match, f"{selector} declares no -webkit-line-clamp"
    return int(match.group(1))


def test_the_monologue_clamps_deep_enough_for_a_viewer_who_cannot_click():
    """An OBS browser source fires no click and no keydown, so whatever the clamp
    hides is hidden from the whole audience permanently. These depths are the
    contract with that audience, not a style preference."""
    assert _clamp_lines(".clamp.think") >= 14
    assert _clamp_lines(".clamp.say") >= 6
    assert _clamp_lines(".tool") >= 3


def test_a_window_capped_turn_count_is_rendered_as_a_lower_bound():
    """turns_lived is counted over a 40-record window, so a longer life leaves a
    count that is a floor. The page carries the same "+" the subject panel uses for
    turns_this_life rather than presenting the floor as an exact count."""
    assert "turns_partial" in HTML


def test_the_dead_panel_counts_how_many_chose():
    assert "chose to die" in HTML
    assert "ended_by_choice" in HTML


def test_the_dead_foot_reads_as_sourced_from_the_notes():
    """`_ending_kind` classifies by reading the tombstone note — the agent's own
    done() message or the harness's synthetic one — so the count is a reading of
    agent-controlled text, not a stage measurement, and the foot must say so."""
    assert "by their own notes, " in HTML


def test_the_grave_labels_attribute_the_ending_to_the_note():
    """The old labels ("ENDED BY ITS OWN HAND") presented the note-head substring
    match as an unhedged verdict. The labels now name the note as the source."""
    assert "ENDED ON ITS OWN NOTE" in HTML
    assert "ENDED ON A HARNESS NOTE" in HTML
    assert "ENDED WITHOUT A NOTE" in HTML
    assert "ENDED BY ITS OWN HAND" not in HTML
    assert "STOPPED BY THE HARNESS" not in HTML
    assert "CAUSE UNRECORDED" not in HTML


def test_the_provenance_line_states_the_containment():
    assert "PROVENANCE_LINES" in HTML
    assert "no network interface" in HTML
    assert "dummy" in HTML
    # the original disclosure is still one of the rotating lines
    assert "the transcript is the proxy's, not the agent's" in HTML


def test_the_containment_facts_hold_the_masthead_slot():
    """The containment facts are the page's load-bearing claims. They render in
    the dominant masthead element (#premise, full-contrast); the aphoristic
    premise sentence is the secondary line (#provenance, faint). The behavioural
    half of this — rotation, offline handling, the death-announcement beat — is
    executed in tests/test_stage_pages_js.py."""
    assert "var lines = provenanceLines();" in HTML
    assert 'setText($("premise"), lines[provenanceAt % lines.length]);' in HTML
    assert 'setText($("provenance"), PREMISE_LINE);' in HTML
    block = HTML[HTML.index("\n#premise {") :]
    block = block[: block.index("}")]
    assert "var(--paper)" in block, block
    assert "var(--paper-dim)" not in block, block


def test_the_stream_page_names_the_repository():
    """The stream page is the only surface strangers reach. The pointer is static
    text, not a link: the page serves an OBS browser source and stays
    non-interactive."""
    assert 'id="repo"' in HTML
    assert "github.com/tachyon-beep/aurora" in HTML
    assert 'href="https://github.com' not in HTML
    assert 'href="http://github.com' not in HTML


def test_the_provenance_rotation_keeps_rotating_under_reduced_motion():
    """The rotation is content, not decoration: prefers-reduced-motion drops only
    the crossfade (swapProvenance falls back to an instant setText) while the
    20s interval keeps running ungated. A viewer who asked for less motion still
    gets every containment fact."""
    assert "if (!REDUCED) setInterval(rotateProvenance" not in HTML
    assert "setInterval(rotateProvenance, 20000);" in HTML
    swap = HTML[HTML.index("function swapProvenance()") : HTML.index("function rotateProvenance()")]
    assert "if (REDUCED) { setText(p, text); return; }" in swap
    # anchored on the declaration, not the bare name, which a CSS comment
    # naming the array would otherwise shadow
    first = HTML[HTML.index("var PROVENANCE_LINES") :]
    first = first[first.index("[") + 1 : first.index("]")].strip().splitlines()[0]
    assert "no network interface" in first, first


def test_the_rotation_swaps_crossfade_and_answer_beats():
    """A beat with a matching containment line pulls the next rotation to it
    once; a fresh generated colour line joins the rotation as one slot, bylined
    as the stage's. The behavioural half runs in tests/test_stage_pages_js.py."""
    assert "BEAT_PREFERRED" in HTML
    assert "reached_out: 0" in HTML
    assert "self_edit: 2" in HTML
    assert "published: 1, spoke: 1" in HTML
    assert '" — the stage"' in HTML
    assert "function noteColour()" in HTML
    assert "< 120000" in HTML


BROADCAST_TYPE_FLOOR = 13

# Every rule that declares its px font size at the 13px floor in
# STREAM_PAGE_HTML's stylesheet, enumerated by grepping the source directly.
# The descendants that only inherit these sizes (.more-label, .g-id, and the
# rest) are covered by their parent and are deliberately not listed. This
# rebuild removed the subject/story/dead panels and the desk
# (`#strip-glyph`, `#subj-strip`, `#now-play`, `#byline`,
# `.grave .g-eyebrow`, `.grave .blk-tomb .more`, `#dead-foot`,
# `.verdict .v-ord`, `.verdict .v-evidence`, `#desk-by`, `#pull-attrib`) and
# added the lineage chart's bar labels and foot and the now panel's evidence
# line (`.bl`, `#lineage-foot`, `.g-eyebrow`, `.g-facts`, `#now-evidence`).
BROADCAST_SMALL_TYPE = (
    "#state-word",
    "#provenance",
    ".ptitle",
    ".eyebrow",
    ".open-tail",
    ".more",
    ".gutter",
    ".gutter .g-mark.end",
    "#pulse-left",
    "#pulse-rate",
    "#return-live",
    "#eye-cap",
    ".subrow",
    ".bl",
    "#lineage-foot",
    ".g-eyebrow",
    ".g-facts",
    "#now-evidence",
    ".rrow .rmeta",
    ".rrow .rid",
    "#said-stamp",
    "#speak-caption",
    "#reached-foot",
    "#stream-foot",
    ".clamp.say::before",
    ".chip b",
    ".divider span",
    "#repo",
    "#to-telemetry",
)


def _declared_size(selector):
    """The px font size one rule declares. Anchored on a newline so `.more` finds
    the rule and not `#recap-box .more`."""
    start = HTML.index("\n" + selector + " {")
    block = HTML[start : HTML.index("}", start)]
    match = re.search(r"font(?:-size)?:[^;]*?(\d+)px", block)
    assert match, f"{selector} declares no px font size"
    return int(match.group(1))


def test_no_broadcast_type_falls_below_the_transcode_floor():
    """At 720p the canvas is downscaled x0.667, at 480p x0.44. Anything under 13px
    here is under 6px for a viewer on a bad connection."""
    sizes = {sel: _declared_size(sel) for sel in BROADCAST_SMALL_TYPE}
    too_small = {sel: size for sel, size in sizes.items() if size < BROADCAST_TYPE_FLOOR}
    assert too_small == {}, f"below the {BROADCAST_TYPE_FLOOR}px floor: {too_small}"


def test_the_rail_is_two_panels_that_fill_the_rail():
    block = HTML[HTML.index("#rail {") : HTML.index("}", HTML.index("#rail {"))]
    declaration = block.split("grid-template-rows:")[1].split(";")[0]
    rows = [int(n) for n in re.findall(r"(\d+)px", declaration)]
    assert rows == [536, 216], rows
    assert sum(rows) + 20 == 772


def test_the_rail_holds_only_the_lineage_and_now():
    rail = HTML[HTML.index('<aside id="rail">') : HTML.index("</aside>")]
    assert 'id="lineage"' in rail and 'id="now"' in rail
    for gone in (
        'id="subject"',
        'id="story"',
        'id="dead"',
        'id="graves"',
        'id="desk"',
        'id="recap-box"',
        'id="pull-box"',
        'id="byline"',
        'id="play-tag"',
        'id="subj-strip"',
        "READ THE REST",
        'class="more"',
    ):
        assert gone not in rail, gone


def test_the_removed_renderers_are_gone_from_the_script():
    for name in (
        "function renderStory",
        "function renderDead",
        "function renderDesk",
        "function deskCycle",
        "function fitRecap",
        "function dropLede",
        "function fallbackRecap",
        "function renderSubject",
        "function makeGrave",
        "function renderStoryByline",
        "graveNodes",
    ):
        assert name not in HTML, name


def test_the_lineage_chart_is_decorative_and_its_facts_are_stated_in_text():
    assert '<div id="chart" aria-hidden="true">' in HTML
    assert 'id="life-figs"' in HTML
    assert 'id="spot-eyebrow"' in HTML and 'id="spot-facts"' in HTML and 'id="spot-note"' in HTML
    assert 'id="lineage-foot"' in HTML


def test_the_colour_line_is_the_now_panels_subject_and_is_announced():
    assert '<p id="now-colour" aria-live="polite"></p>' in HTML
    block = HTML[HTML.index("\n#now-colour {") :]
    block = block[: block.index("}")]
    assert "22px/30px" in block, block
    assert "line-clamp: 3" in block, block
    assert 'id="now-evidence"' in HTML


def test_the_state_strip_left_the_rail_for_the_masthead():
    assert 'id="strip-text"' not in HTML
    assert 'id="strip-glyph"' not in HTML
    assert 'id="state-word"' in HTML


def test_the_lineage_reuses_the_grave_palette_for_bar_kinds():
    css = HTML[HTML.index("<style>") : HTML.index("</style>")]
    assert "#bars .bar.k-declared { background: var(--chosen); }" in css
    assert "#bars .bar.k-harness { background: var(--taken); }" in css
    assert "#bars .bar.k-unknown { background: var(--broken); }" in css
    assert "#bars .bar.now { background: var(--vital);" in css
    assert "\n.bar {" not in css


def test_the_repository_line_survives_the_phone():
    narrow = HTML[HTML.index("@media (max-width: 719px)") :]
    narrow = narrow[: narrow.index("}", narrow.index("}") + 1) + 1]
    assert "#repo" not in narrow
    assert "#provenance { display: none; }" in narrow


def test_the_spotlight_and_foot_are_driven_by_the_tick_not_by_clicks():
    tick = HTML[HTML.index("function tick()") :]
    tick = tick[: tick.index("\n}")]
    for call in (
        "renderLifeFigs(nowMs);",
        "layoutBars(nowMs);",
        "renderSpot(nowMs);",
        "rotateLineageFoot(nowMs);",
        "renderNow();",
    ):
        assert call in tick, call
    assert "deskCycle" not in tick and "fitRecap" not in tick


def test_the_stream_page_stylesheet_declares_no_px_font_size_under_the_floor():
    """The enumerated `BROADCAST_SMALL_TYPE` list is only as good as the reconciliation
    that built it. This scans the actual stylesheet text directly, the same way that
    list was built, so a rule the enumeration missed cannot silently regress below the
    floor. Scoped to STREAM_PAGE_HTML's own <style> block so the console page, which
    is not a broadcast surface, stays out of scope."""
    css = HTML[HTML.index("<style>") : HTML.index("</style>")]
    too_small = [
        (match.group(0).splitlines()[-1].strip(), int(match.group(1)))
        for match in re.finditer(r"font(?:-size)?:[^;]*?(\d+)px", css)
        if int(match.group(1)) < BROADCAST_TYPE_FLOOR
    ]
    assert too_small == [], f"below the {BROADCAST_TYPE_FLOOR}px floor: {too_small}"


def test_a_refused_autoplay_offers_sound_instead_of_only_advancing():
    assert 'id="sound-on"' in HTML
    assert "soundBlocked" in HTML
    # the control is revealed by the refusal path, not rendered unconditionally
    assert HTML.index("soundBlocked") > HTML.index('id="sound-on"')


# ---------- the rebuild: pacing over clicking ----------


def test_the_typewriter_reveals_only_text_that_already_landed():
    """The reveal engine is a delivery mechanism, never a claim: it types out
    reasoning and speech that already arrived, gates on the 90s staleness rule
    (a reload never fakes liveness), skips under reduced motion, and paces to
    the measured cadence clamped to 3-30s. The engine itself is executed in
    tests/test_stage_pages_js.py."""
    assert "function maybeStartReveal()" in HTML
    assert "\n  maybeStartReveal();\n" in HTML, "the call site in reconcileFeed"
    assert "clock() / 1000 - newest.epoch >= 90" in HTML
    assert "Math.max(3000, Math.min(30000, 600 * (g || 0)))" in HTML
    reveal = HTML[HTML.index("function maybeStartReveal()") : HTML.index("/* ---------- feed")]
    assert "if (REDUCED) return;" in reveal


def test_the_reveal_routes_around_the_keyed_reconciliation():
    """updateTurn's setText on a block the engine is animating would jump the
    typewriter to the end on the next poll; the newest turn's think/say text
    goes through the engine while a reveal is active for that key."""
    assert "var revealing = revealActiveFor(key);" in HTML
    assert "if (!revealing) setText(tb.__clamp, reasoning);" in HTML
    assert "if (!revealing) setText(sb.__clamp, content);" in HTML


def test_the_think_block_keeps_tail_mode_until_eviction():
    block = HTML[HTML.index("\n.blk-think.tail .clamp {") :]
    block = block[: block.index("}")]
    assert "display: block" in block
    assert "max-height: 406px" in block
    assert "overflow: hidden" in block
    assert "c.scrollTop = c.scrollHeight;" in HTML


def test_the_metabolism_strip_replaces_the_inflight_row():
    """#inflight narrated absence; #pulse states measured facts: the real
    in-flight request, a 20-bucket token sparkline, tokens per minute. Every
    figure comes from snap.pulse (the recorder's own events)."""
    assert 'id="inflight"' not in HTML
    assert "setInflight" not in HTML
    assert 'id="pulse"' in HTML
    assert 'id="pulse-spark"' in HTML
    assert "snap && snap.pulse" in HTML
    assert "tokens_window" in HTML
    # twenty fixed bars, built once, updated in place — bounded DOM for a
    # page that runs for days
    assert 'for (var i = 0; i < 20; i++) el("div", "bar", spark);' in HTML


def test_the_pulse_strip_shows_only_while_anything_is_alive():
    pulse = HTML[HTML.index("function setPulse(state)") : HTML.index("/* ---------- tick")]
    assert '"live"' in pulse and '"thinking"' in pulse
    assert '"quiet"' in pulse and '"nosignal"' in pulse
    tick = HTML[HTML.index("function tick()") :]
    tick = tick[: tick.index("\n}")]
    assert "setPulse(state);" in tick


def test_programmatic_scrolls_never_unpin_the_feed():
    """The monologue must never silently freeze for the life of an unattended
    browser source: scripted scrolls are flagged and the listener consumes the
    flag; an unpinned feed shows the chip; auto-repin after three minutes."""
    assert "if (programmatic) { programmatic = false; return; }" in HTML
    assert 'id="return-live"' in HTML
    assert "RETURN TO LIVE" in HTML
    assert "Date.now() - lastUserScrollMs <= 180000" in HTML
    assert "expanded.size !== 0" in HTML
    toggle = HTML[HTML.index("function toggle(box)") : HTML.index("function bind(box)")]
    assert "flagProgrammatic(); box.scrollIntoView(" in toggle
    repin = HTML[HTML.index("function repin()") : HTML.index("function updateReturnChip()")]
    assert "flagProgrammatic();" in repin


def test_the_self_ending_turn_is_the_loudest_moment():
    """The premise is a model that can end itself; the page must not render
    self-editing louder than self-termination."""
    assert ".turn.is-edit, .turn.is-error, .turn.is-end {" in HTML
    block = HTML[HTML.index("\n.turn.is-end {") :]
    block = block[: block.index("}")]
    assert "rgba(127,215,182,.10)" in block
    assert "inset 3px 0 0 var(--chosen)" in block


def test_speech_enters_and_evicted_turns_leave_visibly():
    assert "sayin 600ms" in HTML
    assert "@keyframes sayin" in HTML
    assert ".turn.depart" in HTML
    assert "@keyframes depart" in HTML
    # removal on animationend with a fallback timeout, skipped under REDUCED
    evict = HTML[HTML.index("turnNodes.forEach") : HTML.index("dividers.forEach")]
    assert "if (REDUCED) { node.remove(); return; }" in evict
    assert 'node.addEventListener("animationend", drop, { once: true });' in evict
    assert "setTimeout(drop, 400);" in evict


def test_the_death_beat_survives_a_reload():
    """An OBS refresh drops the previous-poll comparison; the beat also fires
    off lineage recency plus a stored marker, every storage access inside
    try/catch. The gate is executed in tests/test_stage_pages_js.py."""
    assert 'window.localStorage.getItem("mournedEpoch")' in HTML
    assert 'window.localStorage.setItem("mournedEpoch"' in HTML
    assert "var mournedMem" in HTML, "the in-memory backstop for broken storage"
    assert "saturate(.2) brightness(.55)" in HTML
    sweep = HTML[HTML.index("\n#death-sweep {") :]
    sweep = sweep[: sweep.index("}")]
    assert "height: 3px" in sweep


def test_a_fresh_edit_shows_the_diff_itself():
    """The show's premise is a model rewriting its own file; for ~45s after
    the stage first sees an edit the panel shows the capped excerpt of the
    actual diff, lines coloured only by their first character and rendered
    via textContent — the text is the agent's."""
    assert 'id="selfmod-diff"' in HTML
    assert "editAge < 45" in HTML
    assert "WHAT IT JUST CHANGED · seen " in HTML
    assert 'el("div", dcls, diffHost).textContent = ln;' in HTML
    for cls in ("d-add", "d-rem", "d-hunk"):
        assert cls in HTML, cls


def test_the_diff_caption_claims_sight_not_authorship():
    """codewatch records when the stage first observed the change; the page
    must not present that as when the agent made it. The caption counts up
    from that first sighting ("seen Ns ago", measured in a real browser at
    1920x1080: "first seen" plus the count overran the 471px title bar by 2px
    and wrapped out of its 24px box) and never wraps."""
    assert "· seen " in HTML
    assert "cannot truthfully claim when the agent made it" in HTML
    title = HTML[HTML.index("\n#selfmod-title {") :]
    title = title[: title.index("}")]
    assert "white-space: nowrap" in title
    assert "text-overflow: ellipsis" in title
    count = HTML[HTML.index("\n#selfmod-count {") :]
    count = count[: count.index("}")]
    assert "flex: none" in count


def test_the_selfmod_title_keeps_its_count_in_both_views():
    assert 'id="selfmod-title"' in HTML
    assert 'id="selfmod-count"' in HTML
    assert '"WHAT IT DID TO ITSELF"' in HTML


def test_the_lane_rows_show_magnitude_not_digit_strings():
    """Per-lane activity bars replace the `12/h · 3.4k tok` strings a casual
    viewer cannot read; a nonzero lane never renders under 4%."""
    assert "l-track" in HTML
    assert "l-fill" in HTML
    assert "Math.max(4, pct)" in HTML
    assert '"/h · "' not in HTML
    assert "laneCount(tok)" in HTML


def test_the_legend_chips_dropped_their_captions():
    assert "private reasoning" not in HTML
    assert "said out loud" not in HTML
    assert "tool calls" not in HTML
    assert ".chip em" not in HTML


def test_the_eye_shows_only_fresh_sense_frames_and_never_blocks_the_feed():
    """Shown only when snap.sense is non-null (the server already gates on
    frame freshness); the img re-sets src only when the url changes; the card
    never intercepts feed scrolling; the caption claims only the ring and the
    capture age. alt is empty — the caption carries the fact."""
    assert 'id="eye"' in HTML
    assert '<img id="eye-img" alt="">' in HTML
    assert '"THE EYE · feed "' in HTML
    assert "img.__src !== sense.url" in HTML
    eye = HTML[HTML.index("\n#eye {") :]
    eye = eye[: eye.index("}")]
    assert "position: absolute" in eye
    assert "pointer-events: none" in eye


def _srgb_luminance(hex_colour):
    channels = []
    for i in (0, 2, 4):
        c = int(hex_colour[i : i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_paper_faint_clears_six_to_one_on_panel_ink():
    """--paper-faint carries 13px chrome everywhere; it was on the AA floor.
    #97a2ab on the panel ink #12171b measures ~6.93:1."""
    assert "--paper-faint: #97a2ab" in HTML
    lighter = _srgb_luminance("97a2ab")
    darker = _srgb_luminance("12171b")
    ratio = (lighter + 0.05) / (darker + 0.05)
    assert ratio >= 6.0, ratio


def test_focus_outlines_are_visible():
    assert (
        ".blk.is-expandable:focus-visible { outline: 2px solid var(--vital); outline-offset: 4px; }"
        in HTML
    )
    assert "#sound-on:focus-visible { outline: 2px solid var(--vital)" in HTML
    assert "#return-live:focus-visible { outline: 2px solid var(--vital)" in HTML


def test_spoken_renders_before_the_ribbon_measures_sparseness():
    body = HTML[HTML.index("function render(prev)") : HTML.index("function poll()")]
    assert body.index("renderSpoken();") < body.index("renderRibbon();")


def test_the_rings_are_uniform():
    """The filled/hollow distinction was never explained on any surface."""
    assert ".ring.filled" not in HTML
    assert '" filled"' not in HTML


def test_the_said_block_announces_politely():
    assert '<div id="reached-said" aria-live="polite">' in HTML


def test_the_selfmod_row_flashes_with_the_subject_cut():
    cut = HTML[HTML.index("function maybeCut()") : HTML.index("function deathBeat")]
    assert "rowflash" in cut
    assert ".rrow.rowflash" in HTML


def test_ellipsized_surfaces_carry_their_full_value_in_a_title():
    assert '$("subj-model").title' in HTML
    assert "node.__name.title" in HTML
    assert "v.title = norm(o.verb" in HTML
    assert "argEl.title = arg;" in HTML
    assert "row.__text.title = prompt;" in HTML


CONSOLE = pages.CONSOLE_PAGE_HTML


def test_console_rows_are_real_buttons():
    """The console is the one stage surface with an actual user. Rows built as divs
    with an onclick are unreachable by keyboard and unnamed to a screen reader."""
    assert 'const row = document.createElement("button")' in CONSOLE
    assert 'const row = document.createElement("div")' not in CONSOLE


def test_console_declares_a_visible_focus_state():
    """Asserting bare ':focus-visible' would stay green even if '.entry:focus-visible'
    were dropped from the selector list while 'select:focus-visible' remained — the
    rows are the navigation surface this task exists for, so pin them specifically."""
    assert ".entry:focus-visible" in CONSOLE


def test_console_root_select_is_labelled():
    assert 'aria-label="browse root"' in CONSOLE


def test_console_points_the_operator_at_the_viewer_for_transcripts():
    """The viewer (loopback port 8090) serves a turn-structured, searchable
    rendering of the same transcripts; the console's raw file listing should
    say so, but only when the transcripts root is being browsed at its top."""
    assert 'id="viewer-note"' in CONSOLE
    assert "http://localhost:8090" in CONSOLE
    assert 'hidden = !(root === "transcripts" && !path)' in CONSOLE


def test_console_surfaces_the_servers_own_error_message():
    """The server distinguishes 'no token configured' from 'invalid token'. Showing
    the operator 'HTTP 401' throws that distinction away."""
    assert 'new Error("HTTP ' not in CONSOLE
    assert "r.json().then" in CONSOLE
    assert "append ?token=" in CONSOLE


def test_console_diff_button_reports_its_own_failure():
    """The diff button is the one action an operator explicitly triggers. Without a
    .catch, a failed request leaves the pane showing stale content with no sign the
    click did anything — a silent wrong answer, not just an ugly one."""
    start = CONSOLE.index('document.getElementById("diff").onclick')
    end = CONSOLE.index("};", start)
    handler = CONSOLE[start:end]
    assert ".catch(err =>" in handler, handler


def test_console_load_restores_focus_into_the_rebuilt_tree():
    """load() rebuilds every row from scratch on each navigation. Without restoring
    focus, a keyboard user's focused button is destroyed, focus reverts to <body>,
    and the next Tab starts over from the top of the document — reachable rows with
    a ring, but no way to actually walk the tree by keyboard."""
    start = CONSOLE.index("function load() {")
    end = CONSOLE.index("\nfunction show(", start)
    body = CONSOLE[start:end]
    assert "document.activeElement" in body, body
    assert re.search(r"hadFocus\b.*\.focus\(\)", body, re.S), body


def test_the_page_has_three_layout_tiers():
    css = HTML[HTML.index("<style>") : HTML.index("</style>")]
    assert "@media (max-width: 1919px)" in css
    assert "@media (max-width: 1199px)" in css
    flow = css[css.index("@media (max-width: 1199px)") :]
    assert "#rail { display: contents; }" in flow
    for rule in (
        "#lineage { order: 1;",
        "#monologue { order: 2;",
        "#now { order: 3;",
        "#ribbon { order: 4;",
    ):
        assert rule in flow, rule
    assert "#eye { display: none; }" in flow
    assert "70dvh" in flow
    assert "min-height: 44px" in flow, "the return-to-live chip must be a touch target"


def test_the_flow_tier_collapses_the_turn_gutter_into_a_row():
    css = HTML[HTML.index("@media (max-width: 1199px)") : HTML.index("</style>")]
    assert ".turn { grid-template-columns: 1fr;" in css
    assert ".col { grid-column: 1; }" in css
    assert ".gutter { grid-column: 1; text-align: left;" in css


def test_the_canvas_tier_alone_hides_overflow():
    css = HTML[HTML.index("<style>") : HTML.index("</style>")]
    base = css[: css.index("@media (max-width: 1919px)")]
    assert "html, body { width: 1920px; height: 1080px;" in base
    scaled = css[css.index("@media (max-width: 1919px)") : css.index("@media (max-width: 1199px)")]
    assert "html, body { width: auto; height: auto; overflow: auto; }" in scaled


def test_the_flow_tier_wraps_the_figures_and_narrows_the_reached_rows():
    flow = HTML[HTML.index("@media (max-width: 1199px)") : HTML.index("@media (min-width: 720px)")]
    assert "#life-figs { white-space: normal; }" in flow
    assert ".g-eyebrow { white-space: normal; }" in flow
    assert "#reached-rows .rrow { grid-template-columns: 82px 1fr 100px; column-gap: 8px; }" in flow


def test_the_reached_meta_never_wraps_into_the_next_row():
    block = HTML[HTML.index("\n.rrow .rmeta {") :]
    block = block[: block.index("}")]
    assert "white-space: nowrap" in block and "text-overflow: ellipsis" in block


def test_bar_labels_overflow_their_cell_rather_than_clip():
    """Only every Nth column carries a label at narrow widths; a two-digit label
    must not be clipped by its own 12-16px cell (its neighbours are empty)."""
    block = HTML[HTML.index("\n.bl {") :]
    block = block[: block.index("}")]
    assert "overflow: visible" in block and "white-space: nowrap" in block
    assert "overflow: hidden" not in block


def test_the_masthead_links_to_the_telemetry_panel():
    """The one link on the broadcast page: internal, to the at-home panel. OBS
    renders it as text; a phone or laptop viewer can follow it."""
    assert '<a id="to-telemetry" href="/telemetry">telemetry →</a>' in HTML
    assert HTML.count("<a ") == 1


def test_the_lineage_foot_never_wraps():
    """A nomination line can outrun the foot's one row; the row clips with an
    ellipsis rather than pushing a second row into the panel."""
    start = HTML.index("\n#lineage-foot {")
    block = HTML[start : HTML.index("}", start)]
    assert "white-space: nowrap" in block and "text-overflow: ellipsis" in block
