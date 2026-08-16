import datetime
import json
import os
import time

import pytest

from stage import desk, llm

RECORDER_SENTINEL = "sk-recorder-DO-NOT-LEAK-0001"
STAGE_KEY = "sk-stage-summary-0002"


@pytest.fixture(autouse=True)
def _clean_desk_state(monkeypatch):
    """Every test starts from a disabled desk with sentinel recorder keys and no transport."""
    desk._reset_for_tests()
    for name in (
        "STAGE_SUMMARY_API_KEY",
        "STAGE_SUMMARY_MODEL",
        "STAGE_ANALYSIS_MODEL",
        "STAGE_ANALYSIS_DURATION_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", RECORDER_SENTINEL)
    monkeypatch.setenv("LLM_API_KEY", RECORDER_SENTINEL)

    def no_network(*args, **kwargs):
        raise AssertionError("the desk tests must never open a transport")

    monkeypatch.setattr(llm, "_send", no_network)
    yield
    desk._reset_for_tests()


def _iso(epoch):
    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc).isoformat()


def _tree(tmp_path, death_epochs, notes=None):
    """A telemetry mirror with one tombstone per death epoch, oldest first."""
    work = tmp_path / "telemetry" / "work"
    tombs = work / "tombstones"
    tombs.mkdir(parents=True)
    for ordinal, epoch in enumerate(death_epochs, start=1):
        path = tombs / f"incarnation-{ordinal:04d}.txt"
        text = (notes or {}).get(ordinal, f"Incarnation ended by done() at turn {ordinal}.")
        path.write_text(text, encoding="utf-8")
        os.utime(path, (epoch, epoch))
    transcript = tmp_path / "agent_life_transcript.jsonl"
    return str(tmp_path / "telemetry"), str(transcript)


def _write_transcript(path, epochs):
    lines = []
    for epoch in epochs:
        lines.append(
            json.dumps(
                {
                    "timestamp": _iso(epoch),
                    "request": {"model": "m"},
                    "response": {"choices": [{"message": {"content": "hello"}}]},
                }
            )
        )
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _turns(*lives):
    return [{"life": life, "epoch": 1000.0 + i} for i, life in enumerate(lives)]


# --- no key, no segment ---------------------------------------------------


def test_disabled_desk_reports_no_verdicts(tmp_path):
    telemetry, transcript = _tree(tmp_path, [time.time() - 1000])
    assert desk.cached_verdicts() is None
    assert desk._refresh_once(telemetry, transcript) is False


def test_a_removed_key_hides_generated_verdicts(tmp_path, monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    monkeypatch.setattr(desk.llm, "chat", lambda *a, **k: "STARS: 3 | A fine life.")
    telemetry, transcript = _tree(tmp_path, [time.time() - 1000])
    desk._refresh_once(telemetry, transcript)
    assert desk.cached_verdicts() is not None
    monkeypatch.delenv("STAGE_SUMMARY_API_KEY")
    assert desk.cached_verdicts() is None


def test_background_thread_does_not_start_without_a_key():
    desk.start_background_refresh("/tmp/telemetry", "/tmp/transcript")
    assert desk._THREAD is None


# --- verdict generation ---------------------------------------------------


def test_a_stubbed_reply_produces_a_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    monkeypatch.setattr(desk.llm, "chat", lambda *a, **k: "STARS: 4 | It built a tool and used it.")
    telemetry, transcript = _tree(tmp_path, [time.time() - 1000])

    assert desk._refresh_once(telemetry, transcript) is True

    cached = desk.cached_verdicts()
    assert cached is not None
    assert cached["model"] == desk.model_name()
    assert cached["duration_seconds"] == desk.DEFAULT_DURATION_SECONDS
    assert cached["generated_at"] > 0
    (verdict,) = cached["verdicts"]
    assert verdict["ordinal"] == 1
    assert verdict["stars"] == 4
    assert verdict["line"] == "It built a tool and used it."
    assert verdict["depth"] == "tombstone_only"
    assert isinstance(verdict["evidence"], str)


@pytest.mark.parametrize(
    "reply",
    [None, "", "Four stars, easily.", "STARS: 6 | out of range", "STARS: | starless"],
)
def test_a_malformed_reply_caches_nothing(tmp_path, monkeypatch, reply):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    monkeypatch.setattr(desk.llm, "chat", lambda *a, **k: reply)
    telemetry, transcript = _tree(tmp_path, [time.time() - 1000])

    assert desk._refresh_once(telemetry, transcript) is True
    assert desk.cached_verdicts() is None


def test_one_call_per_iteration_and_a_stable_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    calls = []

    def fake_chat(system, user, *args, **kwargs):
        calls.append(user)
        return "STARS: 2 | A quiet life."

    monkeypatch.setattr(desk.llm, "chat", fake_chat)
    now = time.time()
    telemetry, transcript = _tree(tmp_path, [now - 5000, now - 3000, now - 1000])

    assert desk._refresh_once(telemetry, transcript) is True
    assert len(calls) == 1
    assert [v["ordinal"] for v in desk.cached_verdicts()["verdicts"]] == [3]

    desk._refresh_once(telemetry, transcript)
    desk._refresh_once(telemetry, transcript)
    assert len(calls) == 3
    assert [v["ordinal"] for v in desk.cached_verdicts()["verdicts"]] == [3, 2, 1]

    assert desk._refresh_once(telemetry, transcript) is False
    assert len(calls) == 3


def test_verdicts_are_bounded_to_the_top_five(tmp_path, monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    calls = []

    def fake_chat(system, user, *args, **kwargs):
        calls.append(user)
        return "STARS: 3 | Middle of the pack."

    monkeypatch.setattr(desk.llm, "chat", fake_chat)
    now = time.time()
    telemetry, transcript = _tree(tmp_path, [now - 7000 + i * 1000 for i in range(6)])

    for _ in range(8):
        desk._refresh_once(telemetry, transcript)

    assert len(calls) == 5
    assert [v["ordinal"] for v in desk.cached_verdicts()["verdicts"]] == [6, 5, 4, 3, 2]


def test_the_verdict_line_and_evidence_caps_hold(tmp_path, monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    monkeypatch.setattr(desk.llm, "chat", lambda *a, **k: "STARS: 5 | " + "x" * 500)
    telemetry, transcript = _tree(tmp_path, [time.time() - 1000])

    desk._refresh_once(telemetry, transcript)

    (verdict,) = desk.cached_verdicts()["verdicts"]
    assert 0 < len(verdict["line"]) <= desk.LINE_CHARS
    assert len(verdict["evidence"]) <= desk.EVIDENCE_CHARS


# --- prompts --------------------------------------------------------------


def test_the_system_prompt_carries_the_form_and_the_framing():
    assert "STARS:" in desk.SYSTEM_PROMPT
    assert llm.RECORDS_FRAMING in desk.SYSTEM_PROMPT


def test_the_model_is_handed_evidence_depth_and_the_note(tmp_path, monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    handed = []

    def fake_chat(system, user, *args, **kwargs):
        handed.append((system, user))
        return "STARS: 1 | The record is thin."

    monkeypatch.setattr(desk.llm, "chat", fake_chat)
    note = "It ended after building a reader over its own registry and leaving a note behind."
    telemetry, transcript = _tree(tmp_path, [time.time() - 1000], notes={1: note})

    desk._refresh_once(telemetry, transcript)

    system, user = handed[0]
    assert "STARS:" in system
    assert llm.RECORDS_FRAMING in system
    assert "tombstone_only" in user
    assert "building a reader" in user
    assert "thin" in user


# --- evidence depth -------------------------------------------------------


def test_depth_full_when_every_counted_turn_survives():
    entry = {
        "turns_lived": 3,
        "turns_partial": False,
        "kind": "declared",
        "lifespan_seconds": 2520.0,
    }
    evidence = desk.life_evidence(2, entry, _turns(1, 2, 2, 2, 3))
    assert evidence["depth"] == "full"
    assert "lived 42m" in evidence["line"]
    assert "3 turns" in evidence["line"]
    assert "ended by its own note" in evidence["line"]


def test_depth_partial_when_the_window_may_miss_the_first_turn():
    entry = {"turns_lived": 2, "turns_partial": True, "kind": "harness"}
    evidence = desk.life_evidence(2, entry, _turns(2, 2))
    assert evidence["depth"] == "partial"
    assert "at least 2 turns" in evidence["line"]
    assert "ended by the harness" in evidence["line"]


def test_depth_partial_when_fewer_turns_survive_than_were_counted():
    entry = {"turns_lived": 5, "turns_partial": False}
    assert desk.life_evidence(2, entry, _turns(1, 2, 2))["depth"] == "partial"


def test_depth_tombstone_only_when_no_turn_survives():
    entry = {"turns_lived": 4, "kind": "declared", "lifespan_seconds": 90.0}
    evidence = desk.life_evidence(2, entry, _turns(3, 3))
    assert evidence["depth"] == "tombstone_only"
    assert "lived 1m" in evidence["line"]


def test_evidence_uses_only_fields_actually_present():
    evidence = desk.life_evidence(1, {}, [])
    assert evidence["depth"] == "tombstone_only"
    assert evidence["line"] == "no measurements on record"
    assert len(evidence["line"]) <= desk.EVIDENCE_CHARS


def test_refresh_classifies_depth_from_the_transcript_tail(tmp_path, monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    monkeypatch.setattr(desk.llm, "chat", lambda *a, **k: "STARS: 3 | Documented.")
    now = time.time()
    first_death, second_death = now - 4000, now - 2000
    telemetry, transcript = _tree(tmp_path, [first_death, second_death])
    _write_transcript(
        transcript,
        [
            first_death - 300,
            first_death - 200,
            first_death + 300,
            first_death + 400,
            first_death + 500,
        ],
    )

    desk._refresh_once(telemetry, transcript)
    desk._refresh_once(telemetry, transcript)

    depths = {v["ordinal"]: v["depth"] for v in desk.cached_verdicts()["verdicts"]}
    assert depths[2] == "full"
    assert depths[1] == "partial"


# --- background thread ----------------------------------------------------


def test_background_thread_starts_once_and_is_a_daemon(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    started = []

    def fake_loop(*args):
        started.append(args)

    monkeypatch.setattr(desk, "_loop", fake_loop)
    desk.start_background_refresh("/tmp/telemetry", "/tmp/transcript")
    first = desk._THREAD
    desk.start_background_refresh("/tmp/telemetry", "/tmp/transcript")
    assert desk._THREAD is first
    assert first.daemon is True
    first.join(timeout=2)
    assert started == [("/tmp/telemetry", "/tmp/transcript")]


# --- configuration --------------------------------------------------------


def test_duration_defaults_and_respects_the_environment(monkeypatch):
    assert desk.duration_seconds() == 20
    monkeypatch.setenv("STAGE_ANALYSIS_DURATION_SECONDS", "45")
    assert desk.duration_seconds() == 45
    monkeypatch.setenv("STAGE_ANALYSIS_DURATION_SECONDS", "soon")
    assert desk.duration_seconds() == 20


def test_model_defaults_to_the_summary_model(monkeypatch):
    assert desk.model_name() == llm.model_name()
    monkeypatch.setenv("STAGE_SUMMARY_MODEL", "vendor/recap")
    assert desk.model_name() == "vendor/recap"
    monkeypatch.setenv("STAGE_ANALYSIS_MODEL", "vendor/analyst")
    assert desk.model_name() == "vendor/analyst"
