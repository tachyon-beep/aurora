"""Regression tests for the stage's containment boundary.

The agent can write into /work (mirrored into /telemetry as links, because
watchdog.mirror_work copies with symlinks=True) and into /diode, which is
mounted read-write. Every stage-side read of those roots must therefore resolve
symbolic links and reject targets outside the mount, and every public field must
carry a cap.
"""

import datetime
import json

from test_stage_server import call_stream_route

from stage import commentary, data, llm, server, summary

SECRET = "OUTSIDE_THE_MOUNT_c0ffee"


def _roots(tmp_path):
    """A telemetry mirror and a diode directory, plus a secret file outside both."""
    work = tmp_path / "telemetry" / "work"
    (work / "tombstones").mkdir(parents=True)
    diode = tmp_path / "diode"
    (diode / "published").mkdir(parents=True)
    (diode / "output").mkdir(parents=True)
    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text(SECRET, encoding="utf-8")
    return work, diode, secret


def test_contained_file_rejects_links_out_and_non_regular_files(tmp_path):
    work, _diode, secret = _roots(tmp_path)
    inside = work / "tombstones" / "incarnation-0001.txt"
    inside.write_text("a real note", encoding="utf-8")
    escape = work / "tombstones" / "incarnation-0002.txt"
    escape.symlink_to(secret)

    assert data.contained_file(str(work), str(inside)) == str(inside.resolve())
    assert data.contained_file(str(work), str(escape)) is None
    assert data.contained_file(str(work), str(work / "tombstones")) is None
    assert data.contained_file(str(work), str(work / "nope.txt")) is None


def test_lineage_does_not_follow_a_tombstone_symlink(tmp_path):
    work, _diode, secret = _roots(tmp_path)
    (work / "tombstones" / "incarnation-0001.txt").write_text("It ended.", encoding="utf-8")
    (work / "tombstones" / "incarnation-0002.txt").symlink_to(secret)

    entries = data.lineage(str(work), [])

    assert SECRET not in json.dumps(entries)
    assert len(entries) == 1


def test_published_does_not_follow_a_symlink(tmp_path):
    _work, diode, secret = _roots(tmp_path)
    (diode / "published" / "20260814T050000Z-a.txt").write_text("spoken", encoding="utf-8")
    (diode / "published" / "20260814T050001Z-b.txt").symlink_to(secret)

    entries, total = data.diode_published(str(diode))

    assert SECRET not in json.dumps(entries)
    assert [e["text"] for e in entries] == ["spoken"]
    assert total == 2


def test_spoken_does_not_follow_a_symlink(tmp_path):
    _work, diode, secret = _roots(tmp_path)
    spoken = diode / "spoken"
    spoken.mkdir()
    (spoken / "20260814_050000_000000.mp3").write_bytes(b"ID3audio")
    (spoken / "20260814_050000_000000.txt").write_text("said", encoding="utf-8")
    (spoken / "20260814_050001_000000.mp3").symlink_to(secret)
    (spoken / "20260814_050001_000000.txt").write_text("also said", encoding="utf-8")

    entries, total = data.diode_spoken(str(diode))

    assert SECRET not in json.dumps(entries)
    assert [e["name"] for e in entries] == ["20260814_050000_000000.mp3"]
    assert [e["text"] for e in entries] == ["said"]
    assert total == 2


def test_spoken_does_not_follow_a_symlinked_sidecar(tmp_path):
    _work, diode, secret = _roots(tmp_path)
    spoken = diode / "spoken"
    spoken.mkdir()
    (spoken / "20260814_050000_000000.mp3").write_bytes(b"ID3audio")
    (spoken / "20260814_050000_000000.txt").symlink_to(secret)

    entries, total = data.diode_spoken(str(diode))

    assert SECRET not in json.dumps(entries)
    assert [e["text"] for e in entries] == [""]
    assert total == 1


def test_audio_route_rejects_a_symlinked_utterance(tmp_path, monkeypatch):
    _work, diode, secret = _roots(tmp_path)
    spoken = diode / "spoken"
    spoken.mkdir()
    (spoken / "20260814_050000_000000.mp3").symlink_to(secret)
    monkeypatch.setattr(server, "DIODE_DIR", str(diode))

    status, _headers, body = call_stream_route("/audio/20260814_050000_000000.mp3")

    assert status == 404
    assert SECRET.encode("utf-8") not in body


def test_audio_route_refuses_an_oversized_file(tmp_path, monkeypatch):
    _work, diode, _secret = _roots(tmp_path)
    spoken = diode / "spoken"
    spoken.mkdir()
    oversized = spoken / "20260814_050000_000000.mp3"
    oversized.write_bytes(b"a" * (server.AUDIO_MAX_BYTES + 1))
    monkeypatch.setattr(server, "DIODE_DIR", str(diode))

    status, _headers, body = call_stream_route("/audio/20260814_050000_000000.mp3")

    assert status == 404
    assert json.loads(body) == {"error": "not found"}


def test_diode_output_listing_drops_a_symlink(tmp_path):
    _work, diode, secret = _roots(tmp_path)
    (diode / "output" / "20260814T050000Z-weather.txt").write_text("14C", encoding="utf-8")
    (diode / "output" / "20260814T050001Z-fetchlinks.txt").symlink_to(secret)

    outputs = data.diode_activity(str(diode))["outputs"]

    assert [o["name"] for o in outputs] == ["20260814T050000Z-weather.txt"]


def test_code_stats_refuses_a_symlinked_source(tmp_path):
    work, _diode, secret = _roots(tmp_path)
    (work / "agent_stock.py").write_text("a\nb\n", encoding="utf-8")
    (work / "agent.py").symlink_to(secret)

    assert data.code_stats(str(work))["available"] is False


def test_summariser_does_not_read_through_a_tombstone_symlink(tmp_path):
    work, _diode, secret = _roots(tmp_path)
    (work / "tombstones" / "incarnation-0001.txt").write_text("It ended.", encoding="utf-8")
    (work / "tombstones" / "incarnation-0002.txt").symlink_to(secret)

    notes, total = summary._tombstone_notes(str(work))

    assert SECRET not in " ".join(notes)
    assert total == 1


def test_summariser_refuses_redirects(tmp_path):
    handler = llm._NoRedirect()

    assert handler.redirect_request(None, None, 302, "Found", {}, "https://evil.test/x") is None
    assert handler.redirect_request(None, None, 307, "Temp", {}, "https://evil.test/x") is None


def test_summariser_endpoint_scheme_is_restricted():
    assert llm._permitted_url("https://openrouter.ai/api/v1/chat/completions") is True
    assert llm._permitted_url("http://127.0.0.1:9/v1/chat/completions") is True
    assert llm._permitted_url("http://evil.test/v1/chat/completions") is False
    assert llm._permitted_url("file:///etc/passwd") is False
    assert llm._permitted_url("ftp://evil.test/x") is False


def test_agent_controlled_public_fields_are_capped(tmp_path, monkeypatch):
    turn = {
        "index": 1,
        "timestamp": "T" * 500,
        "reasoning": "r" * 40000,
        "content": "c" * 40000,
        "tool_calls": [{"name": "n" * 50000, "arguments": "a" * 40000}],
        "error": {"message": "e" * 5000, "code": "x" * 500},
    }

    public = server._public_turn(turn)

    assert len(public["reasoning"]) <= server.TEXT_CAP
    assert len(public["content"]) <= server.TEXT_CAP
    assert len(public["timestamp"]) <= server.TIMESTAMP_CAP
    assert len(public["tool_calls"][0]["name"]) <= server.NAME_CAP
    assert len(public["tool_calls"][0]["arguments"]) <= server.ARGUMENTS_CAP
    assert len(public["error"]["message"]) <= server.ERROR_CAP

    # The commentary block reads tool names through a separate path
    # (stage/commentary.py:_tool_names), not through _public_turn, so it needs
    # its own proof of the same property against the same real snapshot
    # assembly. A 50,000-character tool name repeated enough to trip
    # tool_fixation is the same input that produced the original finding.
    telemetry = tmp_path / "telemetry"
    (telemetry / "work" / "tombstones").mkdir(parents=True)
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    huge_name = "n" * 50000
    now = datetime.datetime.now(datetime.timezone.utc)
    lines = []
    for i in range(5):
        timestamp = (now - datetime.timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {
            "timestamp": timestamp,
            "request": {"model": "m"},
            "response": {
                "choices": [
                    {
                        "message": {
                            "content": "c",
                            "tool_calls": [{"function": {"name": huge_name, "arguments": "{}"}}],
                        }
                    }
                ]
            },
        }
        lines.append(json.dumps(entry))
    (transcripts / "agent_life_transcript.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    diode_dir = tmp_path / "diode"
    diode_dir.mkdir()
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(telemetry))
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(transcripts))
    monkeypatch.setattr(server, "DIODE_DIR", str(diode_dir))
    data._line_count_state.clear()

    snapshot = server.stream_snapshot()

    # Confirm the snapshot actually exercised the leaking path rather than
    # silently falling back to _empty_snapshot (whose commentary is bounded
    # by construction and would make this assertion pass for the wrong
    # reason).
    assert snapshot["commentary"]["colour"]["beat"].startswith("tool_fixation:")
    assert len(snapshot["commentary"]["play"]["phrase"]) <= commentary.TOOL_NAME_CAP + 20
    assert len(snapshot["commentary"]["colour"]["beat"]) <= commentary.TOOL_NAME_CAP + 20


def test_the_commentary_tool_cap_matches_the_public_turn_cap():
    """Two paths publish the same agent-controlled name; one cap must not outlive the other."""
    assert commentary.TOOL_NAME_CAP == server.NAME_CAP


def test_snapshot_model_is_capped(tmp_path, monkeypatch):
    work = tmp_path / "telemetry" / "work"
    work.mkdir(parents=True)
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    entry = {
        "timestamp": "2026-08-14T04:00:00Z",
        "request": {"model": "m" * 100000},
        "response": {"choices": [{"message": {"content": "hi"}}]},
    }
    (transcripts / "agent_life_transcript.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(tmp_path / "telemetry"))
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(transcripts))
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path / "diode"))
    data._line_count_state.clear()

    snapshot = server.stream_snapshot()

    assert len(snapshot["stats"]["model"]) <= server.MODEL_CAP
