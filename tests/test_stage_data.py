import json
import os
import time

from stage import data


def _entry(model="m", content=None, tool_calls=None, error=None, reasoning=None):
    message = {}
    if content is not None:
        message["content"] = content
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    if tool_calls is not None:
        message["tool_calls"] = [{"function": {"name": n, "arguments": a}} for n, a in tool_calls]
    response = {"choices": [{"message": message}]} if not error else {"error": error}
    return {"timestamp": "T", "request": {"model": model, "messages": []}, "response": response}


def _write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def test_load_tail_turns_missing_and_tail(tmp_path):
    assert data.load_tail_turns(str(tmp_path / "absent.jsonl")) == ([], 0)
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [_entry(content=f"c{i}") for i in range(5)])
    turns, total = data.load_tail_turns(str(p), max_turns=2)
    assert total == 5
    assert [t["index"] for t in turns] == [3, 4]
    assert turns[-1]["content"] == "c4"
    assert turns[-1]["model"] == "m"


def test_load_tail_turns_skips_malformed_and_reads_errors(tmp_path):
    p = tmp_path / "t.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(_entry(content="ok")) + "\n")
        f.write("{ half-written\n")
        f.write(json.dumps(_entry(error={"message": "boom"})) + "\n")
    turns, total = data.load_tail_turns(str(p))
    assert total == 3
    assert turns[0]["content"] == "ok"
    assert turns[-1]["error"] == {"message": "boom"}


def test_incarnation_stats(tmp_path):
    stats = data.incarnation_stats(
        [{"index": 6, "model": "deepseek/x", "timestamp": "T6"}], 7, str(tmp_path)
    )
    assert stats == {
        "incarnation": 1,
        "model": "deepseek/x",
        "transcript_turns": 7,
        "turns_this_life": 1,
        "turns_this_life_exact": False,
        "last_timestamp": "T6",
        "last_epoch": None,
        "started_epoch": None,
        "session_file_present": False,
        "lives_ended": 0,
        "ended_by_choice": 0,
        "error_count": 0,
    }
    tomb = tmp_path / "tombstones"
    tomb.mkdir()
    (tomb / "incarnation-1.txt").write_text("a\n", encoding="utf-8")
    (tomb / "incarnation-2.txt").write_text("b\n", encoding="utf-8")
    (tmp_path / "session_context.json").write_text("[]", encoding="utf-8")
    stats = data.incarnation_stats([], 0, str(tmp_path))
    assert stats["incarnation"] == 3
    assert stats["session_file_present"] is True
    assert stats["model"] is None
    assert stats["last_timestamp"] is None


def test_self_modification_events():
    turns = [
        {"index": 0, "tool_calls": [{"name": "read_file", "arguments": "{}"}]},
        {"index": 1, "tool_calls": [{"name": "write_file", "arguments": "A" * 200}]},
        {"index": 2, "tool_calls": [{"name": "done", "arguments": '{"message": "end"}'}]},
    ]
    events = data.self_modification_events(turns)
    assert [e["name"] for e in events] == ["write_file", "done"]
    assert len(events[0]["detail"]) == 120


def test_first_sentence():
    assert data.first_sentence("One. Two. Three.") == "One."
    assert data.first_sentence("no terminator " * 30).endswith("...")
    assert len(data.first_sentence("x" * 500)) <= 143


def test_lineage_prefers_tombstones(tmp_path):
    tomb = tmp_path / "tombstones"
    tomb.mkdir()
    (tomb / "incarnation-20260101_000000_000001-1.txt").write_text(
        "first life ended. more detail.\n", encoding="utf-8"
    )
    (tomb / "incarnation-20260102_000000_000001-1.txt").write_text(
        "second life ended. detail.\n", encoding="utf-8"
    )
    out = data.lineage(str(tmp_path), [], limit=3)
    assert len(out) == 2
    assert out[0]["summary"] == "second life ended."
    assert out[0]["source"] == "tombstone"


def test_lineage_falls_back_to_done_calls(tmp_path):
    turns = [
        {
            "index": 4,
            "tool_calls": [{"name": "done", "arguments": '{"message": "went well. details."}'}],
        },
    ]
    out = data.lineage(str(tmp_path), turns)
    assert out == [
        {
            "source": "transcript",
            "label": "turn 4",
            "summary": "went well.",
            "ordinal": None,
            "kind": "declared",
            "turn": None,
            "ended_epoch": None,
            "sentence": "went well.",
            "sentence_chars": len("went well. details."),
        }
    ]


def test_diode_activity(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    (out_dir / "r1.txt").write_text("result", encoding="utf-8")
    (tmp_path / "console.json").write_text('{"commands": []}', encoding="utf-8")
    got = data.diode_activity(str(tmp_path))
    assert got["outputs"][0]["name"] == "r1.txt"
    assert got["console"] == '{"commands": []}'
    assert got["state"] == ""


def test_load_tail_turns_tolerates_null_request_response(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(
        p,
        [
            _entry(content="ok"),
            {"timestamp": "T", "request": None, "response": None},
        ],
    )
    turns, total = data.load_tail_turns(str(p))
    assert total == 2
    assert len(turns) == 2
    assert turns[0]["content"] == "ok"
    assert turns[1]["model"] is None
    assert turns[1]["content"] is None


def _stamped_entry(i):
    return {"timestamp": f"T{i}", "request": {"model": "m", "messages": []}, "response": {}}


def test_load_tail_turns_reads_only_the_tail_of_large_files(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "TAIL_READ_BYTES", 1000)
    monkeypatch.setattr(data, "_line_count_state", {})
    path = tmp_path / "t.jsonl"
    _write_jsonl(path, [_stamped_entry(i) for i in range(50)])
    turns, total = data.load_tail_turns(str(path), max_turns=5)
    assert total == 50
    assert [t["timestamp"] for t in turns] == ["T45", "T46", "T47", "T48", "T49"]
    assert turns[0]["index"] == 45


def test_load_tail_turns_tracks_growth_and_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_line_count_state", {})
    path = tmp_path / "t.jsonl"
    _write_jsonl(path, [_stamped_entry(i) for i in range(10)])
    _, total = data.load_tail_turns(str(path))
    assert total == 10
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(_stamped_entry(10)) + "\n")
    _, total = data.load_tail_turns(str(path))
    assert total == 11
    _write_jsonl(path, [_stamped_entry(0)])
    _, total = data.load_tail_turns(str(path))
    assert total == 1


def test_load_tail_turns_memory_stays_bounded(tmp_path, monkeypatch):
    import tracemalloc

    monkeypatch.setattr(data, "TAIL_READ_BYTES", 1_000_000)
    monkeypatch.setattr(data, "_line_count_state", {})
    path = tmp_path / "t.jsonl"
    entry = _stamped_entry(0)
    entry["pad"] = "x" * 1000
    line = json.dumps(entry) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        for _ in range(20_000):
            f.write(line)
    tracemalloc.start()
    turns, total = data.load_tail_turns(str(path))
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert total == 20_000
    assert turns
    assert peak < 8_000_000


def test_lineage_reads_only_the_head_of_tombstones(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "TOMBSTONE_READ_BYTES", 64)
    work = tmp_path / "work"
    (work / "tombstones").mkdir(parents=True)
    (work / "tombstones" / "incarnation-1.txt").write_text(
        "Short note. " + "y" * 5000, encoding="utf-8"
    )
    items = data.lineage(str(work), [])
    assert items[0]["summary"] == "Short note."


def test_parse_epoch_tolerates_both_offsets_and_junk():
    assert data.parse_epoch("2026-08-14T04:14:20Z") == 1786680860.0
    assert data.parse_epoch("2026-08-14T04:14:20+00:00") == 1786680860.0
    assert data.parse_epoch("2026-08-14T04:14:20") == 1786680860.0
    assert data.parse_epoch("T6") is None
    assert data.parse_epoch(None) is None
    assert data.parse_epoch("") is None
    assert data.parse_epoch(1234) is None


def test_first_transcript_epoch_reads_only_the_head(tmp_path):
    path = tmp_path / "t.jsonl"
    assert data.first_transcript_epoch(str(path)) is None
    entries = [
        {"timestamp": "2026-08-14T04:11:02Z", "pad": "x" * 200},
        {"timestamp": "2026-08-14T09:00:00Z"},
    ]
    _write_jsonl(path, entries)
    assert data.first_transcript_epoch(str(path)) == data.parse_epoch("2026-08-14T04:11:02Z")
    assert data.first_transcript_epoch(str(path), read_bytes=8) is None


def test_first_transcript_epoch_skips_unparseable_and_untimed_lines(tmp_path):
    path = tmp_path / "t.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        f.write("not json\n")
        f.write(json.dumps({"request": {}}) + "\n")
        f.write(json.dumps({"timestamp": "2026-08-14T04:11:02Z"}) + "\n")
    assert data.first_transcript_epoch(str(path)) == data.parse_epoch("2026-08-14T04:11:02Z")


def _iso(seconds_ago):
    """An ISO-8601 UTC timestamp the given number of seconds in the past."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds_ago))


def _tombstone(work, name, text, age_seconds=None):
    tomb = work / "tombstones"
    tomb.mkdir(parents=True, exist_ok=True)
    path = tomb / name
    path.write_text(text, encoding="utf-8")
    if age_seconds is not None:
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
    return path


def test_tombstone_deaths_uses_mtime_and_guards_absurd_epochs(tmp_path):
    work = tmp_path / "work"
    _tombstone(work, "incarnation-a.txt", "one.", age_seconds=600)
    _tombstone(work, "incarnation-b.txt", "two.", age_seconds=200)
    _tombstone(work, "incarnation-c.txt", "three.", age_seconds=90 * 86400)
    _tombstone(work, "incarnation-d.txt", "four.", age_seconds=-3600)
    deaths = data.tombstone_deaths(str(work))
    assert len(deaths) == 2
    assert deaths == sorted(deaths)


def test_tombstone_deaths_falls_back_to_the_filename_stamp(tmp_path):
    work = tmp_path / "work"
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(time.time() - 3600)) + "_000001"
    path = _tombstone(work, f"incarnation-{stamp}-42.txt", "note.", age_seconds=90 * 86400)
    assert path.exists()
    deaths = data.tombstone_deaths(str(work))
    assert len(deaths) == 1
    assert abs(deaths[0] - (time.time() - 3600)) < 5


def test_classify_life_distinguishes_first_boot_from_undatable_deaths():
    assert data.classify_life(100.0, [], 1) == 1
    assert data.classify_life(100.0, [], 4) is None
    assert data.classify_life(None, [50.0], 2) is None
    assert data.classify_life(100.0, [50.0, 80.0], 3) == 3
    assert data.classify_life(60.0, [50.0, 80.0], 3) == 2
    assert data.classify_life(10.0, [50.0, 80.0], 3) == 1
    assert data.classify_life(100.0, [50.0, 80.0], 2) == 2


def test_annotate_lives_adds_epoch_and_life():
    turns = [{"timestamp": "2026-08-14T04:11:02Z"}, {"timestamp": "bad"}]
    data.annotate_lives(turns, [data.parse_epoch("2026-08-14T04:00:00Z")], 2)
    assert turns[0]["life"] == 2
    assert turns[0]["epoch"] == data.parse_epoch("2026-08-14T04:11:02Z")
    assert turns[1]["epoch"] is None and turns[1]["life"] is None


def test_code_stats_counts_lines_and_memoises(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_code_stat_state", {})
    work = tmp_path / "work"
    work.mkdir()
    assert data.code_stats(str(work)) == {"available": False, "added": 0, "removed": 0}
    (work / "agent_stock.py").write_text("a\nb\nc\n", encoding="utf-8")
    (work / "agent.py").write_text("a\nB\nc\nd\n", encoding="utf-8")
    assert data.code_stats(str(work)) == {"available": True, "added": 2, "removed": 1}
    assert data.code_stats(str(work)) == {"available": True, "added": 2, "removed": 1}
    assert len(data._code_stat_state) == 1
    (work / "agent.py").write_text("a\nb\nc\n", encoding="utf-8")
    os.utime(work / "agent.py", (time.time() + 1, time.time() + 1))
    assert data.code_stats(str(work)) == {"available": True, "added": 0, "removed": 0}


def test_code_stats_reads_at_most_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_code_stat_state", {})
    monkeypatch.setattr(data, "CODE_READ_BYTES", 10)
    work = tmp_path / "work"
    work.mkdir()
    (work / "agent_stock.py").write_text("x\n" * 5000, encoding="utf-8")
    (work / "agent.py").write_text("y\n" * 5000, encoding="utf-8")
    stats = data.code_stats(str(work))
    assert stats["available"] is True
    assert stats["added"] <= 5 and stats["removed"] <= 5


def test_self_modification_events_phrase_what_changed():
    turns = [
        {
            "index": 2,
            "life": 4,
            "tool_calls": [
                {
                    "name": "write_file",
                    "arguments": json.dumps(
                        {"start_line": 88, "end_line": 96, "content": "one\ntwo"}
                    ),
                }
            ],
        },
        {
            "index": 4,
            "life": 4,
            "tool_calls": [
                {"name": "migrate", "arguments": '{"reason": "read_file accepts a path"}'}
            ],
        },
        {"index": 11, "life": 4, "tool_calls": [{"name": "reset", "arguments": "{}"}]},
        {
            "index": 63,
            "life": 4,
            "tool_calls": [{"name": "done", "arguments": '{"message": "it matters. really."}'}],
        },
        {"index": 64, "life": 4, "tool_calls": [{"name": "write_file", "arguments": "not json"}]},
    ]
    events = data.self_modification_events(turns)
    assert [e["kind"] for e in events] == ["write", "migrate", "reset", "done", "write"]
    assert events[0]["headline"] == "REWROTE LINES 88-96"
    assert events[0]["summary"] == "9 lines out, 2 lines in"
    assert events[0]["quoted"] is False
    assert events[1]["headline"] == "RESTARTED INTO NEW SOURCE"
    assert events[1]["summary"] == "read_file accepts a path"
    assert events[1]["quoted"] is True
    assert events[2]["summary"] == "everything written this life is gone"
    assert events[3]["headline"] == "ENDED ITSELF"
    assert events[3]["summary"] == "it matters."
    assert events[3]["quoted"] is True
    assert events[4]["headline"] == "REWROTE ITS OWN SOURCE"
    assert events[4]["summary"] == "not json"


def test_self_modification_events_tolerate_non_integer_line_numbers():
    turns = [
        {
            "index": 1,
            "tool_calls": [
                {"name": "write_file", "arguments": '{"start_line": "a", "end_line": 4}'}
            ],
        },
        {"index": 2, "tool_calls": [{"name": "write_file", "arguments": "[1, 2]"}]},
        {"index": 3, "tool_calls": [{"name": "migrate", "arguments": "{}"}]},
    ]
    events = data.self_modification_events(turns)
    assert [e["headline"] for e in events] == [
        "REWROTE ITS OWN SOURCE",
        "REWROTE ITS OWN SOURCE",
        "RESTARTED INTO NEW SOURCE",
    ]
    assert events[2]["quoted"] is False


def test_self_modification_events_filter_by_life_before_limiting():
    turns = [
        {"index": i, "life": 3, "tool_calls": [{"name": "write_file", "arguments": "{}"}]}
        for i in range(8)
    ]
    turns.append({"index": 9, "life": 4, "tool_calls": [{"name": "write_file", "arguments": "{}"}]})
    events = data.self_modification_events(turns, limit=6, life=4)
    assert [e["index"] for e in events] == [9]
    assert len(data.self_modification_events(turns, limit=6)) == 6


def test_self_modification_events_cap_every_string():
    turns = [
        {
            "index": 1,
            "tool_calls": [
                {"name": "done", "arguments": json.dumps({"message": "z" * 4000})},
                {"name": "write_file", "arguments": "A" * 4000},
            ],
        }
    ]
    for event in data.self_modification_events(turns):
        assert len(event["headline"]) <= 120
        assert len(event["detail"]) <= 120
        assert len(event["summary"]) <= 160


def test_lineage_records_kind_turn_and_time(tmp_path):
    work = tmp_path / "work"
    _tombstone(
        work,
        "incarnation-0009.txt",
        "Incarnation ended by done() at turn 141. It rewrote its clipping function.",
        age_seconds=3600,
    )
    _tombstone(
        work,
        "incarnation-0010.txt",
        "this incarnation was terminated by the harness.\nreason: upstream 400\n",
        age_seconds=1800,
    )
    _tombstone(work, "incarnation-0011.txt", "   ", age_seconds=60)
    items = data.lineage(str(work), [])
    assert [i["label"] for i in items] == [
        "incarnation-0011.txt",
        "incarnation-0010.txt",
        "incarnation-0009.txt",
    ]
    assert [i["ordinal"] for i in items] == [3, 2, 1]
    assert [i["kind"] for i in items] == ["unknown", "harness", "declared"]
    assert items[2]["turn"] == 141
    assert items[1]["turn"] is None
    assert abs(items[0]["ended_epoch"] - (time.time() - 60)) < 5
    assert items[2]["summary"] == "Incarnation ended by done() at turn 141."
    assert items[2]["sentence"] == "Incarnation ended by done() at turn 141."


def test_lineage_sentence_is_longer_than_summary_and_counts_the_whole_head(tmp_path):
    work = tmp_path / "work"
    body = "w" * 400 + ". tail."
    _tombstone(work, "incarnation-1.txt", body, age_seconds=60)
    item = data.lineage(str(work), [])[0]
    assert len(item["summary"]) <= 143
    assert len(item["sentence"]) <= 323
    assert len(item["sentence"]) > len(item["summary"])
    assert item["sentence_chars"] == len(body)


def test_lineage_limit_is_five_by_default(tmp_path):
    work = tmp_path / "work"
    for i in range(7):
        _tombstone(work, f"incarnation-{i}.txt", f"note {i}.", age_seconds=60 + i)
    items = data.lineage(str(work), [])
    assert len(items) == 5
    assert items[0]["ordinal"] == 7


def test_diode_activity_phrases_both_filename_formats(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    (out_dir / "20260814T041050Z-fetchlinks.txt").write_text("x", encoding="utf-8")
    (out_dir / "20260814_041231_004411_weather_51_5__0_1.txt").write_text("y", encoding="utf-8")
    got = data.diode_activity(str(tmp_path))
    by_slug = {o["slug"]: o for o in got["outputs"]}
    assert "fetchlinks" in by_slug
    assert by_slug["fetchlinks"]["verb"] == "followed a link"
    assert by_slug["fetchlinks"]["epoch"] == data.parse_epoch("2026-08-14T04:10:50Z")
    weather = [o for o in got["outputs"] if o["slug"].startswith("weather")][0]
    assert weather["verb"] == "read the weather"
    assert int(weather["epoch"]) == int(data.parse_epoch("2026-08-14T04:12:31Z"))
    assert all(len(o["slug"]) <= 24 and len(o["verb"]) <= 40 for o in got["outputs"])


def test_diode_activity_falls_back_to_mtime_and_default_verb(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    (out_dir / "handmade.txt").write_text("x", encoding="utf-8")
    got = data.diode_activity(str(tmp_path))
    entry = got["outputs"][0]
    assert entry["slug"] == "handmade"
    assert entry["verb"] == "ran handmade"
    assert abs(entry["epoch"] - time.time()) < 60
    assert entry["life"] is None


def test_diode_activity_classifies_life_when_asked(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    (out_dir / "20260814T041050Z-weather.txt").write_text("x", encoding="utf-8")
    deaths = [data.parse_epoch("2026-08-14T04:00:00Z")]
    got = data.diode_activity(str(tmp_path), deaths=deaths, incarnation=2)
    assert got["outputs"][0]["life"] == 2


def test_diode_published_missing_directory(tmp_path):
    assert data.diode_published(str(tmp_path)) == ([], 0)


def test_diode_published_reads_newest_first_and_caps_text(tmp_path):
    pub = tmp_path / "published"
    pub.mkdir()
    (pub / "20260814_041231_004411.txt").write_text("q" * 900, encoding="utf-8")
    (pub / "20260814_041000_000001.txt").write_text("older", encoding="utf-8")
    (pub / "20260813_041000_000001.txt").write_text("oldest", encoding="utf-8")
    items, total = data.diode_published(str(tmp_path))
    assert total == 3
    assert len(items) == 2
    assert items[0]["name"] == "20260814_041231_004411.txt"
    assert len(items[0]["text"]) == 400
    assert items[0]["chars"] == 900
    assert int(items[0]["epoch"]) == int(data.parse_epoch("2026-08-14T04:12:31Z"))
    assert items[1]["text"] == "older"


def test_diode_published_reads_only_the_head_of_huge_files(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "PUBLISHED_READ_BYTES", 32)
    pub = tmp_path / "published"
    pub.mkdir()
    (pub / "20260814_041231_004411.txt").write_text("p" * 100_000, encoding="utf-8")
    items, total = data.diode_published(str(tmp_path))
    assert total == 1
    assert len(items[0]["text"]) == 32
    assert items[0]["chars"] == 100_000


def test_incarnation_stats_counts_this_life_and_endings(tmp_path):
    work = tmp_path / "work"
    _tombstone(work, "incarnation-1.txt", "ended by choice.", age_seconds=3600)
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [{"timestamp": _iso(7200)}])
    deaths = data.tombstone_deaths(str(work))
    turns = [
        {"index": 0, "model": "m", "timestamp": _iso(120)},
        {"index": 1, "model": "m", "timestamp": _iso(60), "error": {"code": 400}},
    ]
    data.annotate_lives(turns, deaths, 2)
    entries = data.lineage(str(work), turns)
    stats = data.incarnation_stats(
        turns,
        2,
        str(work),
        display_turns=turns[-1:],
        lineage_entries=entries,
        deaths=deaths,
        transcript_path=str(transcript),
    )
    assert stats["incarnation"] == 2
    assert stats["lives_ended"] == 1
    assert stats["ended_by_choice"] == 1
    assert stats["error_count"] == 1
    assert stats["turns_this_life"] == 2
    assert stats["turns_this_life_exact"] is False
    assert stats["last_epoch"] == data.parse_epoch(_iso(60))
    assert stats["started_epoch"] == deaths[-1]


def test_incarnation_stats_marks_a_partial_window_exact(tmp_path):
    work = tmp_path / "work"
    _tombstone(work, "incarnation-1.txt", "gone.", age_seconds=3600)
    deaths = data.tombstone_deaths(str(work))
    turns = [{"timestamp": _iso(7200)}, {"timestamp": _iso(60)}]
    data.annotate_lives(turns, deaths, 2)
    stats = data.incarnation_stats(turns, 2, str(work), deaths=deaths)
    assert stats["turns_this_life"] == 1
    assert stats["turns_this_life_exact"] is True


def test_incarnation_stats_on_first_boot_counts_every_turn(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    turns = [{"timestamp": "2026-08-14T04:11:02Z"}, {"timestamp": "2026-08-14T04:12:02Z"}]
    data.annotate_lives(turns, [], 1)
    assert [t["life"] for t in turns] == [1, 1]
    stats = data.incarnation_stats(turns, 2, str(work), deaths=[])
    assert stats["turns_this_life"] == 2
    assert stats["incarnation"] == 1


def test_diode_activity_orders_mixed_filename_formats_by_time(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    (out_dir / "20260814T041050Z-weather.txt").write_text("x", encoding="utf-8")
    (out_dir / "20260814_050000_000001_entropy.txt").write_text("x", encoding="utf-8")
    (out_dir / "20260814T030000Z-wikipedia.md").write_text("x", encoding="utf-8")
    outputs = data.diode_activity(str(tmp_path))["outputs"]
    assert [o["slug"] for o in outputs] == ["entropy", "weather", "wikipedia"]
