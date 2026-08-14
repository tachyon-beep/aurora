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
    """`#said` is a flex column inside a 136px ribbon row and `#said-foot` carries
    `margin-top: auto`, so anything after the foot is pushed past the panel's
    `overflow: hidden` edge and never reads. The caption must render whether or not
    playback succeeds, so its position is behaviour, not decoration."""
    assert HTML.index('id="said-text"') < HTML.index('id="speak-caption"')
    assert HTML.index('id="speak-caption"') < HTML.index('id="said-foot"')


def test_the_caption_clamps_the_published_line_to_make_room():
    assert "#said.is-captioned #said-text" in HTML
    assert 'setClass($("said"), "is-captioned"' in HTML


def test_the_caption_replaces_the_placeholder_that_would_contradict_it():
    """`renderRibbon` writes "It has said nothing to anyone outside." into `#said-text`
    whenever nothing has been published, and marks a publication with `.spoke`. Speech
    is not publication, so an utterance before the first publish would leave that
    sentence sitting directly above a caption of what was just said. The placeholder is
    hidden in that state and the caption takes the panel's line."""
    assert "#said.is-captioned:not(.spoke) #said-text { display: none; }" in HTML
    assert "#said.is-captioned:not(.spoke) #speak-caption" in HTML


def test_the_panel_accent_lights_for_an_utterance_as_well_as_a_publication():
    assert "#said.is-captioned { border-left: 2px solid var(--say); }" in HTML


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
    assert "spokenQueue.push(name)" in HTML
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


def test_stream_page_renders_lanes():
    html = pages.STREAM_PAGE_HTML
    assert 'id="lanes"' in html
    assert "renderLanes" in html
    assert "snap.lanes" in html
    assert "in_flight_since" in html
    assert "tokens_hour" in html
