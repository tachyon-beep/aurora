import re

from stage import commentary, pages

HTML = pages.STREAM_PAGE_HTML


def test_the_now_block_exists_above_the_recap():
    assert 'id="now"' in HTML
    assert HTML.index('id="now"') < HTML.index('id="recap-box"')


def test_the_commentary_never_borrows_the_subjects_registers():
    """The commentator is a third register. Mistaking it for the subject is the one
    thing this block must never do, so the guard reads sentinels rather than trying
    to infer where the CSS ends."""
    start = HTML.index("/* commentary:start */")
    end = HTML.index("/* commentary:end */")
    block = HTML[start:end]
    assert "#now-colour" in block, "the sentinels do not span the whole block"
    assert "#now-by" in block, "the sentinels do not span the whole block"
    for token in ("--think", "--say", "--act", "--serif"):
        assert token not in block, token


def test_the_commentator_is_bylined_as_not_the_subject():
    assert "the stage, not the subject" in HTML


def test_the_page_never_writes_commentary_with_inner_html():
    for line in HTML.split("\n"):
        if "innerHTML" in line:
            assert "commentary" not in line and "colour" not in line and "play" not in line


def test_the_recap_drops_its_opening_sentence():
    assert "dropLede" in HTML
    # Pins the call site, not just the function definition: deleting the call
    # from renderStory would leave every behavioural test below green even
    # though dropLede would never run.
    assert "text = dropLede(text);" in HTML


def _drop_lede(text):
    """A Python mirror of the page's `dropLede`, built from literals pulled out of the
    live source rather than hand-copied, so a change to the JS's split pattern or
    threshold breaks this extraction instead of silently drifting out of sync.

    `dropLede` is pure JS embedded in `STREAM_PAGE_HTML`; there is no JS runtime in this
    test environment (no `node` dependency anywhere else in the suite, no JS-execution
    package installed), so it cannot be called directly from pytest. This mirrors the
    codebase's existing pattern for pinning JS behaviour from Python
    (`tests/test_stage_commentary.py::test_silence_threshold_matches_the_pages_state_ladder`
    extracts a numeric threshold from `stage/pages.py` source the same way).
    """
    assert "text.split(/(?<=\\.)\\s+/)" in HTML, (
        "dropLede's split pattern changed; update this test"
    )
    match = re.search(r"if \(parts\.length >= (\d+)\) return parts\.slice\(1\)", HTML)
    assert match, "dropLede's threshold check changed; update this test"
    threshold = int(match.group(1))
    parts = re.split(r"(?<=\.)\s+", text)
    if len(parts) >= threshold:
        return " ".join(parts[1:])
    return text


def test_drop_lede_removes_the_first_sentence_at_three_or_more():
    assert _drop_lede("A. B. C.") == "B. C."
    assert _drop_lede("A. B. C. D.") == "B. C. D."


def test_drop_lede_leaves_exactly_two_sentences_unchanged():
    assert _drop_lede("A. B.") == "A. B."


def test_drop_lede_leaves_a_single_sentence_unchanged():
    assert _drop_lede("Only one sentence.") == "Only one sentence."


def test_drop_lede_leaves_an_empty_string_unchanged_and_does_not_throw():
    assert _drop_lede("") == ""


def test_drop_lede_leaves_text_with_no_sentence_boundary_unchanged():
    assert _drop_lede("No periods here at all") == "No periods here at all"


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


def test_the_queue_advances_on_every_way_playback_can_stop():
    # An unhandled failure would leave spokenBusy set and wedge the queue. That
    # the advance happens exactly once per item is executed, not grepped, in
    # tests/test_stage_pages_js.py.
    assert 'a.addEventListener("ended", spokenAdvance)' in HTML
    assert 'a.addEventListener("error", spokenAdvance)' in HTML
    assert "if (mine === spokenCurrent) spokenAdvance();" in HTML


def test_the_played_set_survives_a_reload():
    # In memory only, an OBS scene switch with "refresh browser when scene
    # becomes active" replays the last utterance inside the freshness window.
    assert 'window.localStorage.getItem("spokenPlayed")' in HTML
    assert 'window.localStorage.setItem("spokenPlayed"' in HTML
    assert "SPOKEN_MEMORY" in HTML


def test_a_future_dated_utterance_never_plays():
    # The agent can write into /diode/spoken, and a stamp in the future would
    # otherwise stay inside the freshness window forever.
    assert "if (ageMs < 0 || ageMs > 180000) continue;" in HTML


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


def test_a_grave_shows_derived_facts_above_the_note():
    """The stage states what it measured; the agent's own note stays below it."""
    assert "lifespan_seconds" in HTML
    assert "turns_lived" in HTML
    assert "g-facts" in HTML
    assert "clamp tomb" in HTML, "the note itself must survive the rebuild"


def test_the_dead_panel_counts_how_many_chose():
    assert "chose to die" in HTML
    assert "ended_by_choice" in HTML


def test_the_provenance_line_states_the_containment():
    assert "PROVENANCE_LINES" in HTML
    assert "no network interface" in HTML
    assert "dummy" in HTML
    # the original disclosure is still one of the rotating lines
    assert "the transcript is the proxy's, not the agent's" in HTML


def test_the_provenance_rotation_still_states_the_containment_when_paused():
    """prefers-reduced-motion stops the rotation, so whichever line is showing must
    be one that still carries a containment fact — not a bare refresh notice."""
    rotation = HTML[HTML.index("PROVENANCE_LINES") :]
    rotation = rotation[: rotation.index("/* ---------- render ----------")]
    assert "REDUCED" in rotation
    first = HTML[HTML.index("PROVENANCE_LINES") :]
    first = first[first.index("[") + 1 : first.index("]")].strip().splitlines()[0]
    assert "no network interface" in first, first
