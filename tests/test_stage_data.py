import json

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
        "last_timestamp": "T6",
        "session_file_present": False,
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
    assert out == [{"source": "transcript", "label": "turn 4", "summary": "went well."}]


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
