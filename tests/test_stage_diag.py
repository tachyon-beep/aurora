import json
import os

import datetime
import time

from stage import diag

BASE = int(time.time()) - 7200


def _epoch(minute, second=0):
    return float(BASE + minute * 60 + second)


def _stamp(minute, second=0):
    moment = datetime.datetime.fromtimestamp(_epoch(minute, second), datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry(
    minute=0,
    second=0,
    stream="core",
    model="m",
    content=None,
    reasoning=None,
    tool_calls=None,
    error=None,
    tools=True,
    usage=None,
):
    message = {}
    if content is not None:
        message["content"] = content
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    if tool_calls is not None:
        message["tool_calls"] = [{"function": {"name": n, "arguments": a}} for n, a in tool_calls]
    if error:
        response = {"error": error}
    else:
        response = {"choices": [{"message": message}]}
    if usage is not None:
        response["usage"] = usage
    request = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
    if tools:
        request["tools"] = [{"type": "function"}]
    return {
        "timestamp": _stamp(minute, second),
        "stream": stream,
        "request": request,
        "response": response,
    }


def _write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _append_jsonl(path, entries):
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _tombstone(work_dir, name, text, minute):
    tomb = os.path.join(work_dir, "tombstones")
    os.makedirs(tomb, exist_ok=True)
    path = os.path.join(tomb, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    stamp = _epoch(minute)
    os.utime(path, (stamp, stamp))
    return path


def _events(path, events):
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


# ---------- incarnations ----------


def test_incarnations_lists_every_life_newest_first(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    work = str(tmp_path / "work")
    os.makedirs(work)
    _write_jsonl(
        transcript,
        [
            _entry(minute=1, content="a"),
            _entry(minute=2, content="b"),
            _entry(minute=11, content="c"),
        ],
    )
    _tombstone(work, "incarnation-1.txt", "chose to end. turn 2 reached.", minute=10)
    lives = diag.incarnations(transcript, work, now=_epoch(20))
    assert [life["ordinal"] for life in lives] == [2, 1]
    assert lives[0]["current"] is True
    assert lives[1]["current"] is False
    assert lives[1]["turns"] == 2
    assert lives[0]["turns"] == 1
    assert lives[1]["ended_epoch"] == _epoch(10)
    assert lives[0]["ended_epoch"] is None
    assert lives[1]["summary"].startswith("chose to end.")


def test_incarnations_without_tombstones_reports_one_current_life(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    work = str(tmp_path / "work")
    os.makedirs(work)
    _write_jsonl(transcript, [_entry(minute=1)])
    lives = diag.incarnations(transcript, work, now=_epoch(20))
    assert len(lives) == 1
    assert lives[0]["ordinal"] == 1
    assert lives[0]["current"] is True
    assert lives[0]["turns"] == 1


def test_incarnations_counts_subcalls_and_errors_separately(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    work = str(tmp_path / "work")
    os.makedirs(work)
    _write_jsonl(
        transcript,
        [
            _entry(minute=1, content="a"),
            _entry(minute=2, tools=False, content="sub"),
            _entry(minute=3, error={"message": "boom"}),
        ],
    )
    lives = diag.incarnations(transcript, work, now=_epoch(20))
    assert lives[0]["turns"] == 2
    assert lives[0]["subcalls"] == 1
    assert lives[0]["errors"] == 1


def test_incarnations_excludes_stream_entries_from_turn_counts(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    work = str(tmp_path / "work")
    os.makedirs(work)
    _write_jsonl(
        transcript,
        [
            _entry(minute=1, content="a"),
            _entry(minute=2, stream="scout", tools=False, content="s"),
        ],
    )
    lives = diag.incarnations(transcript, work, now=_epoch(20))
    assert lives[0]["turns"] == 1
    assert lives[0]["subcalls"] == 0


# ---------- incarnation turns ----------


def test_incarnation_turns_filters_by_life_and_paginates_newest_first(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    work = str(tmp_path / "work")
    os.makedirs(work)
    _write_jsonl(
        transcript,
        [_entry(minute=m, content=f"c{m}") for m in (1, 2, 3)]
        + [_entry(minute=m, content=f"c{m}") for m in (11, 12, 13)],
    )
    _tombstone(work, "incarnation-1.txt", "note", minute=10)
    page = diag.incarnation_turns(transcript, work, life=1, now=_epoch(20))
    assert page["total"] == 3
    assert [t["content"] for t in page["turns"]] == ["c3", "c2", "c1"]
    page = diag.incarnation_turns(transcript, work, life=2, offset=1, limit=1, now=_epoch(20))
    assert page["total"] == 3
    assert [t["content"] for t in page["turns"]] == ["c12"]
    assert page["offset"] == 1


def test_incarnation_turns_carry_full_detail_with_operator_caps(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    work = str(tmp_path / "work")
    os.makedirs(work)
    long_text = "x" * (diag.FIELD_CAP + 100)
    _write_jsonl(
        transcript,
        [
            _entry(
                minute=1,
                reasoning=long_text,
                content="body",
                tool_calls=[("write_file", json.dumps({"content": "y" * 5000}))],
                usage={"total_tokens": 7},
            )
        ],
    )
    page = diag.incarnation_turns(transcript, work, life=1, now=_epoch(20))
    turn = page["turns"][0]
    assert turn["index"] == 0
    assert turn["kind"] == "loop"
    assert turn["model"] == "m"
    assert turn["stream"] == "core"
    assert turn["reasoning"] == "x" * diag.FIELD_CAP
    assert turn["reasoning_truncated"] is True
    assert turn["reasoning_chars"] == diag.FIELD_CAP + 100
    assert turn["content"] == "body"
    assert turn["content_truncated"] is False
    call = turn["tool_calls"][0]
    assert call["name"] == "write_file"
    assert len(call["arguments"]) > 5000
    assert call["arguments_truncated"] is False
    assert turn["error"] is None


def test_incarnation_turns_skips_malformed_lines_but_keeps_indices(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    work = str(tmp_path / "work")
    os.makedirs(work)
    with open(transcript, "w", encoding="utf-8") as f:
        f.write(json.dumps(_entry(minute=1, content="a")) + "\n")
        f.write("{ half-written\n")
        f.write(json.dumps(_entry(minute=2, content="b")) + "\n")
    page = diag.incarnation_turns(transcript, work, life=1, now=_epoch(20))
    assert [t["index"] for t in page["turns"]] == [2, 0]


def test_incarnation_turns_places_unparseable_timestamps_in_the_current_life(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    work = str(tmp_path / "work")
    os.makedirs(work)
    entry = _entry(minute=1, content="lost")
    entry["timestamp"] = "not a timestamp"
    _write_jsonl(transcript, [entry])
    _tombstone(work, "incarnation-1.txt", "note", minute=10)
    page = diag.incarnation_turns(transcript, work, life=2, now=_epoch(20))
    assert [t["content"] for t in page["turns"]] == ["lost"]
    assert diag.incarnation_turns(transcript, work, life=1, now=_epoch(20))["turns"] == []


# ---------- raw entries ----------


def test_entry_returns_the_raw_record_by_index(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    _write_jsonl(transcript, [_entry(minute=1, content="a"), _entry(minute=2, content="b")])
    record = diag.entry(transcript, 1)
    assert record["index"] == 1
    assert '"b"' in record["raw"]
    assert record["truncated"] is False
    assert diag.entry(transcript, 5) is None
    assert diag.entry(transcript, -1) is None


def test_entry_caps_oversized_records(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    _write_jsonl(transcript, [_entry(minute=1, content="z" * (diag.RAW_CAP + 500))])
    record = diag.entry(transcript, 0)
    assert record["truncated"] is True
    assert len(record["raw"]) == diag.RAW_CAP
    assert record["chars"] > diag.RAW_CAP


# ---------- index maintenance ----------


def test_index_picks_up_appended_entries(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    work = str(tmp_path / "work")
    os.makedirs(work)
    _write_jsonl(transcript, [_entry(minute=1, content="a")])
    assert diag.incarnations(transcript, work, now=_epoch(20))[0]["turns"] == 1
    _append_jsonl(transcript, [_entry(minute=2, content="b")])
    assert diag.incarnations(transcript, work, now=_epoch(20))[0]["turns"] == 2


def test_index_resets_when_the_transcript_shrinks(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    work = str(tmp_path / "work")
    os.makedirs(work)
    _write_jsonl(transcript, [_entry(minute=1, content=f"c{i}") for i in range(5)])
    assert diag.incarnations(transcript, work, now=_epoch(20))[0]["turns"] == 5
    _write_jsonl(transcript, [_entry(minute=2, content="after")])
    lives = diag.incarnations(transcript, work, now=_epoch(20))
    assert lives[0]["turns"] == 1


def test_index_ignores_a_partial_trailing_line_until_completed(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    work = str(tmp_path / "work")
    os.makedirs(work)
    full = json.dumps(_entry(minute=1, content="whole"))
    partial = json.dumps(_entry(minute=2, content="tail"))
    with open(transcript, "w", encoding="utf-8") as f:
        f.write(full + "\n")
        f.write(partial[:20])
    assert diag.incarnations(transcript, work, now=_epoch(20))[0]["turns"] == 1
    with open(transcript, "a", encoding="utf-8") as f:
        f.write(partial[20:] + "\n")
    assert diag.incarnations(transcript, work, now=_epoch(20))[0]["turns"] == 2


def test_missing_transcript_yields_empty_shapes(tmp_path):
    transcript = str(tmp_path / "absent.jsonl")
    work = str(tmp_path / "work")
    os.makedirs(work)
    lives = diag.incarnations(transcript, work, now=_epoch(20))
    assert len(lives) == 1
    assert lives[0]["turns"] == 0
    page = diag.incarnation_turns(transcript, work, life=1, now=_epoch(20))
    assert page == {"life": 1, "total": 0, "offset": 0, "turns": []}


# ---------- streams ----------


def test_streams_lists_every_lane_with_lifetime_totals(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    events = str(tmp_path / "events.jsonl")
    _write_jsonl(
        transcript,
        [
            _entry(minute=1, content="a"),
            _entry(minute=2, stream="scout", tools=False, content="s"),
        ],
    )
    old = _stamp(-240)
    _events(
        events,
        [
            {"timestamp": _stamp(1), "event": "bind", "stream": "scout"},
            {"timestamp": _stamp(2), "event": "open", "stream": "scout", "id": "a1", "model": "sm"},
            {
                "timestamp": _stamp(2, 30),
                "event": "close",
                "stream": "scout",
                "id": "a1",
                "status": 200,
                "usage": {"total_tokens": 40},
            },
            {"timestamp": old, "event": "open", "stream": "idle", "id": "b1"},
            {
                "timestamp": old,
                "event": "close",
                "stream": "idle",
                "id": "b1",
                "status": 500,
                "usage": {"total_tokens": 7},
            },
            {"timestamp": old, "event": "unbind", "stream": "idle"},
        ],
    )
    lanes = diag.streams(transcript, events, now=_epoch(30))
    names = [lane["name"] for lane in lanes]
    assert names[0] == "core"
    assert set(names) == {"core", "scout", "idle"}
    scout = next(lane for lane in lanes if lane["name"] == "scout")
    assert scout["bound"] is True
    assert scout["requests_hour"] == 1
    assert scout["requests_total"] == 1
    assert scout["tokens_total"] == 40
    assert scout["transcript_entries"] == 1
    idle = next(lane for lane in lanes if lane["name"] == "idle")
    assert idle["bound"] is False
    assert idle["requests_hour"] == 0
    assert idle["requests_total"] == 1
    assert idle["errors_total"] == 1
    assert idle["tokens_total"] == 7
    core = lanes[0]
    assert core["transcript_entries"] == 1


def test_streams_includes_lanes_seen_only_in_the_transcript(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    events = str(tmp_path / "events.jsonl")
    _write_jsonl(transcript, [_entry(minute=1, stream="ghost", tools=False)])
    _events(events, [])
    lanes = diag.streams(transcript, events, now=_epoch(30))
    ghost = next(lane for lane in lanes if lane["name"] == "ghost")
    assert ghost["transcript_entries"] == 1
    assert ghost["requests_total"] == 0


def test_stream_requests_filters_and_paginates_newest_first(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    _write_jsonl(
        transcript,
        [
            _entry(minute=1, content="a"),
            _entry(
                minute=2,
                stream="scout",
                tools=False,
                content="s1",
                usage={"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            ),
            _entry(minute=3, stream="scout", tools=False, error={"message": "bad"}),
            _entry(minute=4, stream="other", tools=False, content="x"),
        ],
    )
    page = diag.stream_requests(transcript, "scout")
    assert page["total"] == 2
    assert [r["index"] for r in page["requests"]] == [2, 1]
    newest, oldest = page["requests"]
    assert newest["error"] == "bad"
    assert oldest["total_tokens"] == 7
    assert oldest["prompt_tokens"] == 3
    assert oldest["model"] == "m"
    assert oldest["content_chars"] == 2
    page = diag.stream_requests(transcript, "scout", offset=1, limit=1)
    assert [r["index"] for r in page["requests"]] == [1]


def test_stream_requests_treats_untagged_entries_as_core(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    entry = _entry(minute=1, content="old")
    del entry["stream"]
    _write_jsonl(transcript, [entry])
    page = diag.stream_requests(transcript, "core")
    assert page["total"] == 1
    assert page["requests"][0]["content_chars"] == 3


def test_incarnations_counts_self_edits_per_life(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    work = str(tmp_path / "work")
    os.makedirs(work)
    _write_jsonl(
        transcript,
        [
            _entry(minute=1, tool_calls=[("write_file", "{}"), ("migrate", "{}")]),
            _entry(minute=2, tool_calls=[("reset", "{}"), ("done", "{}")]),
            _entry(minute=3, tools=False, tool_calls=[("write_file", "{}")]),
            _entry(minute=11, tool_calls=[("write_file", "{}")]),
        ],
    )
    _tombstone(work, "incarnation-1.txt", "chose to end. turn 2 reached.", minute=10)
    lives = diag.incarnations(transcript, work, now=_epoch(20))
    assert lives[1]["edits"] == 2
    assert lives[0]["edits"] == 1


def test_life_turns_yields_one_life_oldest_first_without_subcalls(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    work = str(tmp_path / "work")
    os.makedirs(work)
    _write_jsonl(
        transcript,
        [
            _entry(minute=1, content="a"),
            _entry(minute=2, tools=False, content="sub"),
            _entry(minute=3, content="b"),
            _entry(minute=11, content="c"),
        ],
    )
    with open(transcript, "a", encoding="utf-8") as f:
        f.write("not json\n")
    _tombstone(work, "incarnation-1.txt", "chose to end.", minute=10)
    turns = list(diag.life_turns(transcript, work, 1, now=_epoch(20)))
    assert [index for index, _epoch_, _entry_ in turns] == [0, 2]
    assert [e["response"]["choices"][0]["message"]["content"] for _i, _e, e in turns] == ["a", "b"]
    assert turns[0][1] == _epoch(1)
    current = list(diag.life_turns(transcript, work, 2, now=_epoch(20)))
    assert [index for index, _epoch_, _entry_ in current] == [3]
    assert list(diag.life_turns(str(tmp_path / "missing.jsonl"), work, 1, now=_epoch(20))) == []


def test_incarnations_mark_counts_inexact_when_a_death_cannot_be_dated_or_an_entry_placed(tmp_path):
    transcript = str(tmp_path / "t.jsonl")
    work = str(tmp_path / "work")
    os.makedirs(work)
    _write_jsonl(transcript, [_entry(minute=1), _entry(minute=11)])
    _tombstone(work, "incarnation-1.txt", "chose to end.", minute=10)
    assert all(life["exact"] for life in diag.incarnations(transcript, work, now=_epoch(20)))
    # An entry with no timestamp folds into the current life: inexact.
    bad = _entry(minute=12)
    bad["timestamp"] = "not a time"
    _append_jsonl(transcript, [bad])
    lives = diag.incarnations(transcript, work, now=_epoch(20))
    assert not any(life["exact"] for life in lives)
    assert lives[0]["turns"] == 2
    # A tombstone dated absurdly far in the past cannot be placed: inexact.
    transcript2 = str(tmp_path / "t2.jsonl")
    work2 = str(tmp_path / "work2")
    os.makedirs(work2)
    _write_jsonl(transcript2, [_entry(minute=1), _entry(minute=11)])
    path = _tombstone(work2, "incarnation-1.txt", "chose to end.", minute=10)
    ancient = _epoch(0) - 400 * 86400
    os.utime(path, (ancient, ancient))
    lives = diag.incarnations(transcript2, work2, now=_epoch(20))
    assert [life["ordinal"] for life in lives] == [2, 1]
    assert not any(life["exact"] for life in lives)
