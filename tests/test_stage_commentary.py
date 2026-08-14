import re

from stage import commentary


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
