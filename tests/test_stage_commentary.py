import re

import pytest

from stage import commentary


@pytest.fixture(autouse=True)
def _clean_commentary_state():
    commentary._reset_for_tests()
    yield
    commentary._reset_for_tests()


def _turn(index, epoch, tools=(), error=None, reasoning=""):
    return {
        "index": index,
        "epoch": epoch,
        "error": error,
        "reasoning": reasoning,
        "tool_calls": [{"name": n, "arguments": "{}"} for n in tools],
    }


def _stats(**kw):
    base = {"incarnation": 4, "turns_this_life": 20, "started_epoch": 1000.0, "error_count": 0}
    base.update(kw)
    return base


NOW = 10_000.0
EMPTY_DIODE = {"outputs": []}


def test_done_call_is_the_loudest_beat():
    turns = [_turn(1, NOW - 5, tools=("write_file",)), _turn(2, NOW - 1, tools=("done",))]
    beat = commentary.detect_beat(turns, _stats(), EMPTY_DIODE, [], NOW)
    assert beat["kind"] == "ending"


def test_a_young_life_beats_a_self_edit():
    turns = [_turn(1, NOW - 2, tools=("write_file",))]
    beat = commentary.detect_beat(turns, _stats(turns_this_life=1), EMPTY_DIODE, [], NOW)
    assert beat["kind"] == "new_life"


def test_self_edit_names_the_tool_that_made_it():
    turns = [_turn(i, NOW - 60 + i, tools=("read_file",)) for i in range(4)]
    turns.append(_turn(9, NOW - 5, tools=("migrate",)))
    beat = commentary.detect_beat(turns, _stats(), EMPTY_DIODE, [], NOW)
    assert beat["kind"] == "self_edit"
    assert beat["tool"] == "migrate"


def test_repeat_failure_needs_two_errors_in_the_window():
    one = [_turn(i, NOW - 30 + i, tools=("read_file",)) for i in range(5)]
    one.append(_turn(9, NOW - 2, tools=("read_file",), error="boom"))
    assert commentary.detect_beat(one, _stats(), EMPTY_DIODE, [], NOW)["kind"] != "repeat_failure"

    two = list(one)
    two[3] = _turn(3, NOW - 27, tools=("read_file",), error="boom")
    beat = commentary.detect_beat(two, _stats(), EMPTY_DIODE, [], NOW)
    assert beat["kind"] == "repeat_failure"
    assert beat["count"] == 2


def test_a_stale_error_burst_gives_way_to_silence():
    """An error burst is an event, not a state — it must age out like the others."""
    turns = [
        _turn(1, NOW - 4000, tools=("read_file",), error="boom"),
        _turn(2, NOW - 3990, tools=("read_file",), error="boom"),
    ]
    beat = commentary.detect_beat(turns, _stats(), EMPTY_DIODE, [], NOW)
    assert beat["kind"] == "silence"


def test_published_beats_a_plain_diode_output():
    diode = {"outputs": [{"command": "weather", "epoch": NOW - 20, "life": 4}]}
    published = [{"epoch": NOW - 10, "text": "hello"}]
    beat = commentary.detect_beat([_turn(1, NOW - 5)], _stats(), diode, published, NOW)
    assert beat["kind"] == "published"


def test_a_stale_published_entry_gives_way_to_the_next_beat():
    """The published gate is independently reachable — a stale entry must not win."""
    published = [{"epoch": NOW - 200, "text": "hello"}]
    beat = commentary.detect_beat([_turn(1, NOW - 5)], _stats(), EMPTY_DIODE, published, NOW)
    assert beat["kind"] == "working"


def test_reached_out_carries_the_command_word():
    diode = {"outputs": [{"command": "weather", "epoch": NOW - 20, "life": 4}]}
    beat = commentary.detect_beat([_turn(1, NOW - 5)], _stats(), diode, [], NOW)
    assert beat["kind"] == "reached_out"
    assert beat["detail"] == "weather"
    assert beat["novelty"] == "first_this_life"


def test_a_second_output_of_the_same_command_is_a_repeat():
    diode = {
        "outputs": [
            {"command": "weather", "epoch": NOW - 20, "life": 4},
            {"command": "weather", "epoch": NOW - 90, "life": 4},
        ]
    }
    beat = commentary.detect_beat([_turn(1, NOW - 5)], _stats(), diode, [], NOW)
    assert beat["novelty"] == "repeat"


def test_stale_evidence_gives_way_to_silence():
    diode = {"outputs": [{"command": "weather", "epoch": NOW - 4000, "life": 4}]}
    turns = [_turn(1, NOW - 4000, tools=("write_file",))]
    beat = commentary.detect_beat(turns, _stats(), diode, [], NOW)
    assert beat["kind"] == "silence"
    assert beat["span_seconds"] >= commentary.SILENCE_SECONDS


def test_tool_fixation_needs_three_of_the_same_tool():
    turns = [_turn(i, NOW - 20 + i, tools=("read_file",)) for i in range(3)]
    beat = commentary.detect_beat(turns, _stats(), EMPTY_DIODE, [], NOW)
    assert beat["kind"] == "tool_fixation"
    assert beat["tool"] == "read_file"
    assert beat["count"] == 3


def test_long_think_fires_on_an_outlier_reasoning_block():
    turns = [_turn(i, NOW - 40 + i, reasoning="x" * 100) for i in range(5)]
    turns.append(_turn(9, NOW - 2, reasoning="x" * 5000))
    beat = commentary.detect_beat(turns, _stats(), EMPTY_DIODE, [], NOW)
    assert beat["kind"] == "long_think"


def test_working_is_the_floor():
    turns = [_turn(i, NOW - 10 + i, tools=(f"tool_{i}",)) for i in range(3)]
    beat = commentary.detect_beat(turns, _stats(), EMPTY_DIODE, [], NOW)
    assert beat["kind"] == "working"


def test_a_beat_is_always_returned_for_degenerate_input():
    for turns in ([], [{}], [{"index": 1}]):
        beat = commentary.detect_beat(turns, {}, {}, [], NOW)
        assert beat["kind"]
        assert beat["id"]


def test_beat_id_is_kind_plus_one_coarse_discriminator():
    diode_a = {"outputs": [{"command": "weather", "epoch": NOW - 10, "life": 4}]}
    diode_b = {"outputs": [{"command": "arxiv", "epoch": NOW - 10, "life": 4}]}
    a = commentary.detect_beat([_turn(1, NOW - 5)], _stats(), diode_a, [], NOW)
    b = commentary.detect_beat([_turn(1, NOW - 5)], _stats(), diode_b, [], NOW)
    assert a["id"] == "reached_out:weather"
    assert b["id"] == "reached_out:arxiv"


def test_beat_id_ignores_counts_and_spans():
    """A beat id that moved with its counters would defeat the regeneration floor."""
    few = [_turn(i, NOW - 20 + i, tools=("read_file",)) for i in range(3)]
    many = [_turn(i, NOW - 20 + i, tools=("read_file",)) for i in range(6)]
    a = commentary.detect_beat(few, _stats(), EMPTY_DIODE, [], NOW)
    b = commentary.detect_beat(many, _stats(), EMPTY_DIODE, [], NOW)
    assert a["id"] == b["id"]
    assert a["count"] != b["count"]


def test_silence_threshold_matches_the_pages_state_ladder():
    """SILENCE_SECONDS mirrors the boundary at which the masthead says QUIET."""
    with open("stage/pages.py", "r", encoding="utf-8") as f:
        source = f.read()
    match = re.search(r"if \(age < (\d+)\) return \"quiet\"", source)
    assert match, "the quiet boundary moved or was renamed"
    thinking = re.search(r"if \(age < (\d+)\) return \"thinking\"", source)
    assert thinking, "the thinking boundary moved or was renamed"
    assert commentary.SILENCE_SECONDS == int(thinking.group(1))


def test_every_beat_kind_has_a_template():
    for kind in commentary.BEAT_KINDS:
        assert kind in commentary.BEAT_TEMPLATES


def test_no_beat_can_render_an_empty_line():
    for kind in commentary.BEAT_KINDS:
        beat = commentary._beat(kind, tool="read_file", detail="weather", count=3, span=120.0)
        line = commentary.template_line(beat)
        assert line.strip()
        assert "{" not in line and "}" not in line


def test_template_line_survives_a_beat_with_every_field_missing():
    for kind in commentary.BEAT_KINDS:
        line = commentary.template_line({"kind": kind})
        assert line.strip()
        assert "None" not in line


def test_play_by_play_names_the_newest_tool():
    turns = [_turn(1, NOW - 30, tools=("read_file",))]
    play = commentary.play_by_play(turns, EMPTY_DIODE, _stats())
    assert play["tag"]
    assert play["phrase"]
    assert play["epoch"] == NOW - 30


def test_play_by_play_is_never_empty_without_turns():
    play = commentary.play_by_play([], EMPTY_DIODE, {})
    assert play["tag"]
    assert play["phrase"]


def test_a_read_is_never_phrased_as_a_self_edit():
    """data._phrase_event falls through to REWROTE ITS OWN SOURCE for every non-edit tool."""
    for name in ("read_file", "validate", "list_dir", "llm"):
        play = commentary.play_by_play([_turn(1, NOW - 5, tools=(name,))], EMPTY_DIODE, _stats())
        assert "rewrot" not in play["phrase"].lower(), name
        assert "rewrit" not in play["phrase"].lower(), name


def test_an_agent_built_tool_still_gets_a_tag_and_a_phrase():
    play = commentary.play_by_play([_turn(1, NOW - 5, tools=("summarise",))], EMPTY_DIODE, _stats())
    assert play["tag"] == "SU"
    assert play["phrase"].strip()


def test_colour_falls_back_to_the_template_without_a_key(monkeypatch):
    monkeypatch.delenv("STAGE_SUMMARY_API_KEY", raising=False)
    beat = commentary._beat("self_edit", tool="write_file")
    line = commentary.colour_line(beat)
    assert line["text"] == commentary.BEAT_TEMPLATES["self_edit"]
    assert line["generated"] is False


def test_a_generated_line_never_survives_a_beat_change(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", "k")
    monkeypatch.setattr(commentary.llm, "chat", lambda *a, **k: "It is rewriting itself, live.")
    edit = commentary._beat("self_edit", tool="write_file")
    commentary.publish_beat(edit)
    commentary._refresh_if_due({}, now=1000.0)
    assert commentary.colour_line(edit)["generated"] is True

    reach = commentary._beat("reached_out", detail="weather")
    got = commentary.colour_line(reach)
    assert got["generated"] is False
    assert got["text"] == commentary.BEAT_TEMPLATES["reached_out"]


def test_the_regeneration_floor_holds_against_a_beat_storm(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", "k")
    calls = []
    monkeypatch.setattr(commentary.llm, "chat", lambda *a, **k: calls.append(1) or "A line.")
    state = {}
    commentary.publish_beat(commentary._beat("self_edit", tool="write_file"))
    assert commentary._refresh_if_due(state, now=1000.0) is True
    for offset in range(1, int(commentary.MIN_REGEN_SECONDS)):
        commentary.publish_beat(commentary._beat("reached_out", detail=str(offset)))
        commentary._refresh_if_due(state, now=1000.0 + offset)
    assert len(calls) == 1


def test_the_same_beat_is_not_regenerated(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", "k")
    calls = []
    monkeypatch.setattr(commentary.llm, "chat", lambda *a, **k: calls.append(1) or "A line.")
    state = {}
    beat = commentary._beat("working")
    commentary.publish_beat(beat)
    commentary._refresh_if_due(state, now=1000.0)
    commentary._refresh_if_due(state, now=1000.0 + 10 * commentary.MIN_REGEN_SECONDS)
    assert len(calls) == 1


def test_a_failed_generation_leaves_the_template_showing(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", "k")
    monkeypatch.setattr(commentary.llm, "chat", lambda *a, **k: None)
    beat = commentary._beat("silence", span=200.0)
    commentary.publish_beat(beat)
    commentary._refresh_if_due({}, now=1000.0)
    line = commentary.colour_line(beat)
    assert line["text"] == commentary.BEAT_TEMPLATES["silence"]
    assert line["generated"] is False


def test_the_colour_prompt_carries_the_shared_injection_framing():
    assert commentary.llm.RECORDS_FRAMING in commentary.COLOUR_SYSTEM_PROMPT


def test_the_model_is_handed_the_beat_and_never_the_raw_stream(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", "k")
    seen = {}

    def fake_chat(system, user, *a, **k):
        seen["system"], seen["user"] = system, user
        return "A line."

    monkeypatch.setattr(commentary.llm, "chat", fake_chat)
    commentary.publish_beat(commentary._beat("reached_out", detail="weather", count=2))
    commentary._refresh_if_due({}, now=1000.0)
    assert "reached_out" in seen["user"]
    assert "weather" in seen["user"]
    assert len(seen["user"]) < 600


def test_background_thread_starts_once_and_is_a_daemon(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", "k")
    monkeypatch.setattr(commentary.time, "sleep", lambda _s: (_ for _ in ()).throw(SystemExit))
    commentary.start_background_refresh()
    first = commentary._THREAD
    commentary.start_background_refresh()
    assert commentary._THREAD is first
    assert first.daemon is True


def test_background_thread_does_not_start_without_a_key(monkeypatch):
    monkeypatch.delenv("STAGE_SUMMARY_API_KEY", raising=False)
    commentary.start_background_refresh()
    assert commentary._THREAD is None


def test_source_never_names_the_recorder_credentials():
    with open("stage/commentary.py", "r", encoding="utf-8") as f:
        source = f.read()
    assert "OPENROUTER_API_KEY" not in source
    assert "LLM_API_KEY" not in source
