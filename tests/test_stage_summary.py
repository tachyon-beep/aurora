import json
import threading

import pytest

from stage import llm, summary

RECORDER_SENTINEL = "sk-recorder-DO-NOT-LEAK-0001"
STAGE_KEY = "sk-stage-summary-0002"


@pytest.fixture(autouse=True)
def _clean_summary_state(monkeypatch):
    """Every test starts from a disabled summariser with the recorder keys set as sentinels."""
    summary._reset_for_tests()
    for name in (
        "STAGE_SUMMARY_API_KEY",
        "STAGE_SUMMARY_BASE_URL",
        "STAGE_SUMMARY_MODEL",
        "STAGE_SUMMARY_INTERVAL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", RECORDER_SENTINEL)
    monkeypatch.setenv("LLM_API_KEY", RECORDER_SENTINEL)
    yield
    summary._reset_for_tests()


class _Response:
    def __init__(self, body, status=200):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status

    def read(self, *args):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_transport(monkeypatch, handler):
    """Replace the transport with handler(request) and record every request object."""
    calls = []

    def fake_send(request, timeout=None):
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(llm, "_send", fake_send)
    return calls


def _reply(text):
    return _Response(json.dumps({"choices": [{"message": {"content": text}}]}))


def _ok(text="A recap sentence. Another one."):
    return lambda request: _reply(text)


def _fixture_tree(
    tmp_path, tombstones=("Incarnation ended by done() at turn 63. It left a note.",)
):
    telemetry = tmp_path / "telemetry"
    work = telemetry / "work"
    tombs = work / "tombstones"
    tombs.mkdir(parents=True)
    for i, text in enumerate(tombstones, start=1):
        (tombs / f"incarnation-{i:04d}.txt").write_text(text, encoding="utf-8")
    (work / "agent_stock.py").write_text("a\nb\nc\n", encoding="utf-8")
    (work / "agent.py").write_text("a\nb\nc\nd\n", encoding="utf-8")
    transcript = tmp_path / "agent_life_transcript.jsonl"
    entry = {
        "timestamp": "T",
        "request": {"model": "deepseek/deepseek-v4-pro"},
        "response": {
            "choices": [
                {
                    "message": {
                        "content": "hello",
                        "reasoning_content": "thinking",
                        "tool_calls": [
                            {"function": {"name": "write_file", "arguments": '{"start_line": 1}'}}
                        ],
                    }
                }
            ]
        },
    }
    transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    return str(telemetry), str(transcript)


# --- disabled by default -------------------------------------------------


def test_disabled_by_default(tmp_path, monkeypatch):
    telemetry, transcript = _fixture_tree(tmp_path)

    def explode(request, timeout=None):
        raise AssertionError("the summariser made a request while disabled")

    monkeypatch.setattr(llm, "_send", explode)
    monkeypatch.setattr(llm.urllib.request, "urlopen", explode)
    monkeypatch.setattr(llm.urllib.request, "build_opener", explode)

    assert summary.enabled() is False
    assert summary.cached_story() is None
    assert summary.start_background_refresh(telemetry, transcript) is None
    assert not any(t.name == "stage-summary" for t in threading.enumerate())
    assert summary._refresh_if_due(dict(summary._STATE), telemetry, transcript, now=0.0) is False
    assert summary.cached_story() is None


def test_empty_string_key_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", "   ")
    assert summary.enabled() is False


def test_empty_string_config_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_BASE_URL", "")
    monkeypatch.setenv("STAGE_SUMMARY_MODEL", "")
    monkeypatch.setenv("STAGE_SUMMARY_INTERVAL_SECONDS", "")
    assert summary.base_url() == "https://openrouter.ai/api/v1"
    assert summary.model_name() == "anthropic/claude-sonnet-5"
    assert summary.interval_seconds() == 300


def test_interval_never_raises_on_garbage(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_INTERVAL_SECONDS", "soon")
    assert summary.interval_seconds() == 300
    monkeypatch.setenv("STAGE_SUMMARY_INTERVAL_SECONDS", "-4")
    assert summary.interval_seconds() == 300
    monkeypatch.setenv("STAGE_SUMMARY_INTERVAL_SECONDS", "45")
    assert summary.interval_seconds() == 45


# --- the recorder credential never leaves the stage ----------------------


def test_recorder_key_never_leaks_when_summariser_disabled(tmp_path, monkeypatch):
    """The recorder sentinel is set, no stage key is: nothing is sent at all."""
    telemetry, transcript = _fixture_tree(tmp_path)
    calls = _install_transport(monkeypatch, _ok())
    summary._refresh_if_due(summary._STATE, telemetry, transcript, now=0.0)
    assert calls == []
    assert summary.cached_story() is None


def test_recorder_key_never_appears_in_an_outbound_request(tmp_path, monkeypatch):
    telemetry, transcript = _fixture_tree(tmp_path)
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    calls = _install_transport(monkeypatch, _ok())

    assert summary._refresh_if_due(summary._STATE, telemetry, transcript, now=0.0) is True
    assert len(calls) == 1
    request = calls[0]
    wire = [request.full_url, request.data.decode("utf-8")]
    wire.extend(str(v) for v in request.headers.values())
    wire.extend(str(k) for k in request.headers)
    for part in wire:
        assert RECORDER_SENTINEL not in part
    assert any(STAGE_KEY in part for part in wire)
    assert request.full_url == "https://openrouter.ai/api/v1/chat/completions"


def test_stage_service_carries_only_its_own_summary_key():
    with open("docker-compose.yml", "r", encoding="utf-8") as f:
        text = f.read()
    stage_block = text.split("\n  stage:\n")[1].split("\n  cloudflared:\n")[0]
    for name in (
        "STAGE_SUMMARY_API_KEY: ${STAGE_SUMMARY_API_KEY:-}",
        "STAGE_SUMMARY_BASE_URL: ${STAGE_SUMMARY_BASE_URL:-}",
        "STAGE_SUMMARY_MODEL: ${STAGE_SUMMARY_MODEL:-}",
        "STAGE_SUMMARY_INTERVAL_SECONDS: ${STAGE_SUMMARY_INTERVAL_SECONDS:-}",
        "STAGE_COMMENTARY_MODEL: ${STAGE_COMMENTARY_MODEL:-}",
        "STAGE_COMMENTARY_INTERVAL_SECONDS: ${STAGE_COMMENTARY_INTERVAL_SECONDS:-}",
    ):
        assert name in stage_block
    assert "OPENROUTER_API_KEY" not in stage_block
    assert "LLM_API_KEY" not in stage_block
    assert "STAGE_COMMENTARY_API_KEY" not in stage_block


def test_env_example_documents_the_summariser():
    with open(".env.example", "r", encoding="utf-8") as f:
        text = f.read()
    assert "STAGE_SUMMARY_API_KEY=" in text
    assert "#STAGE_SUMMARY_BASE_URL=https://openrouter.ai/api/v1" in text
    assert "#STAGE_SUMMARY_MODEL=anthropic/claude-sonnet-5" in text
    assert "#STAGE_SUMMARY_INTERVAL_SECONDS=300" in text
    assert "#STAGE_COMMENTARY_MODEL=anthropic/claude-haiku-4-5-20251001" in text
    assert "#STAGE_COMMENTARY_INTERVAL_SECONDS=60" in text


def test_module_source_never_names_the_recorder_credentials():
    for path in ("stage/summary.py", "stage/llm.py"):
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        assert "OPENROUTER_API_KEY" not in source, path
        assert "LLM_API_KEY" not in source, path


def test_recap_prompt_carries_the_shared_injection_framing():
    assert llm.RECORDS_FRAMING in summary.SYSTEM_PROMPT


# --- fail open ------------------------------------------------------------


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(
            lambda request: (_ for _ in ()).throw(TimeoutError("timed out")), id="timeout"
        ),
        pytest.param(lambda request: (_ for _ in ()).throw(OSError("dns")), id="network"),
        pytest.param(lambda request: _Response("{}", status=500), id="non-200"),
        pytest.param(lambda request: _Response("not json at all"), id="malformed-json"),
        pytest.param(lambda request: _Response(json.dumps({"choices": []})), id="empty-choices"),
        pytest.param(lambda request: _Response(json.dumps({"choices": [{}]})), id="no-message"),
        pytest.param(lambda request: _reply(""), id="empty-content"),
        pytest.param(lambda request: _reply(None), id="null-content"),
    ],
)
def test_every_failure_class_fails_open(tmp_path, monkeypatch, handler):
    telemetry, transcript = _fixture_tree(tmp_path)
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    _install_transport(monkeypatch, handler)
    assert summary._refresh_if_due(summary._STATE, telemetry, transcript, now=0.0) is True
    assert summary.cached_story() is None


def test_failure_leaves_a_previous_story_in_place(tmp_path, monkeypatch):
    telemetry, transcript = _fixture_tree(tmp_path)
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    _install_transport(monkeypatch, _ok("The first recap."))
    summary._refresh_if_due(summary._STATE, telemetry, transcript, now=0.0)
    assert summary.cached_story()["text"] == "The first recap."

    _install_transport(monkeypatch, lambda request: _Response("{}", status=503))
    summary._STATE["last_incarnation"] = 99  # force a death-triggered regeneration
    summary._refresh_if_due(summary._STATE, telemetry, transcript, now=1000.0)
    assert summary.cached_story()["text"] == "The first recap."


def test_missing_telemetry_tree_still_produces_a_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    calls = _install_transport(monkeypatch, _ok())
    absent = str(tmp_path / "nothing")
    assert summary._refresh_if_due(summary._STATE, absent, absent + "/t.jsonl", now=0.0) is True
    assert len(calls) == 1
    assert "END RECORDS" in calls[0].data.decode("utf-8")


# --- caps -----------------------------------------------------------------


def test_output_cap_holds_without_any_sentence_boundary(tmp_path, monkeypatch):
    telemetry, transcript = _fixture_tree(tmp_path)
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    _install_transport(monkeypatch, _ok("word " * 1200))
    summary._refresh_if_due(summary._STATE, telemetry, transcript, now=0.0)
    story = summary.cached_story()
    assert len(story["text"]) <= summary.MAX_OUTPUT_CHARS
    assert story["model"] == "anthropic/claude-sonnet-5"
    assert story["generated_at"] > 0


def test_output_cut_prefers_a_sentence_boundary():
    text = ("Sentence about the agent. " * 200) + "trailing fragment without an end"
    cleaned = llm.clean(text, summary.MAX_OUTPUT_CHARS)
    assert len(cleaned) <= summary.MAX_OUTPUT_CHARS
    assert cleaned.endswith(".")


def test_output_is_flattened_to_one_paragraph():
    cleaned = llm.clean(
        "```\n# Heading\nLine one.\n\n\n   Line   two.\n```", summary.MAX_OUTPUT_CHARS
    )
    assert cleaned == "Heading Line one. Line two."


def test_prompt_cap_holds_against_oversized_tombstones(tmp_path):
    telemetry, transcript = _fixture_tree(tmp_path, tombstones=["x" * 9000] * 5)
    collected = summary._collect(telemetry, transcript)
    prompt = collected["prompt"]
    assert len(prompt) <= summary.MAX_PROMPT_CHARS
    assert prompt.endswith(summary.CLOSING_INSTRUCTION)
    # each note is trimmed to TOMBSTONE_CHARS first, so the newest still survives whole
    assert "x" * summary.TOMBSTONE_CHARS in prompt
    assert "x" * (summary.TOMBSTONE_CHARS + 1) not in prompt


def test_section_capper_drops_oldest_sections_then_hard_slices():
    sections = ["BEGIN", "a" * 400, "b" * 400, "c" * 400]
    assert summary._cap_sections(sections, 1000) == "BEGIN\n" + "a" * 400 + "\n" + "b" * 400
    assert len(summary._cap_sections(["z" * 5000], 100)) == 100


def test_prompt_carries_tombstones_newest_first(tmp_path):
    telemetry, transcript = _fixture_tree(
        tmp_path,
        tombstones=[
            "Incarnation ended by done() at turn 10. Older note.",
            "Incarnation terminated by the harness at turn 88. Newer note.",
        ],
    )
    prompt = summary._collect(telemetry, transcript)["prompt"]
    assert prompt.index("Newer note") < prompt.index("Older note")
    assert "ended by the harness" in prompt
    assert "ended by its own hand" in prompt
    assert "1 line added and 0 removed" in prompt


# --- refresh policy -------------------------------------------------------


def test_digest_ignores_elapsed_time(tmp_path, monkeypatch):
    telemetry, transcript = _fixture_tree(tmp_path)
    monkeypatch.setattr(summary.time, "time", lambda: 1_000_000.0)
    first = summary._collect(telemetry, transcript)
    monkeypatch.setattr(summary.time, "time", lambda: 1_000_600.0)
    later = summary._collect(telemetry, transcript)
    assert first["digest_material"] == later["digest_material"]


def test_unchanged_digest_does_not_regenerate(tmp_path, monkeypatch):
    telemetry, transcript = _fixture_tree(tmp_path)
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    calls = _install_transport(monkeypatch, _ok())
    assert summary._refresh_if_due(summary._STATE, telemetry, transcript, now=0.0) is True
    assert summary._refresh_if_due(summary._STATE, telemetry, transcript, now=10_000.0) is False
    assert len(calls) == 1


def test_changed_digest_regenerates_after_the_interval(tmp_path, monkeypatch):
    telemetry, transcript = _fixture_tree(tmp_path)
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    calls = _install_transport(monkeypatch, _ok())
    summary._refresh_if_due(summary._STATE, telemetry, transcript, now=0.0)
    with open(telemetry + "/work/agent.py", "a", encoding="utf-8") as f:
        f.write("e\nf\n")
    assert summary._refresh_if_due(summary._STATE, telemetry, transcript, now=100.0) is False
    assert summary._refresh_if_due(summary._STATE, telemetry, transcript, now=400.0) is True
    assert len(calls) == 2


def test_incarnation_change_forces_regeneration_before_the_interval(tmp_path, monkeypatch):
    telemetry, transcript = _fixture_tree(tmp_path)
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    calls = _install_transport(monkeypatch, _ok())
    summary._refresh_if_due(summary._STATE, telemetry, transcript, now=0.0)
    tomb = tmp_path / "telemetry" / "work" / "tombstones" / "incarnation-0002.txt"
    tomb.write_text("Incarnation terminated by the harness at turn 4.", encoding="utf-8")
    assert summary._refresh_if_due(summary._STATE, telemetry, transcript, now=61.0) is True
    assert len(calls) == 2


def test_sixty_second_floor_holds_against_a_failing_endpoint(tmp_path, monkeypatch):
    telemetry, transcript = _fixture_tree(tmp_path)
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    calls = _install_transport(monkeypatch, lambda request: _Response("{}", status=503))
    assert summary._refresh_if_due(summary._STATE, telemetry, transcript, now=0.0) is True
    tomb = tmp_path / "telemetry" / "work" / "tombstones" / "incarnation-0002.txt"
    tomb.write_text("Incarnation ended by done() at turn 4.", encoding="utf-8")
    for moment in (5.0, 30.0, 59.9):
        assert summary._refresh_if_due(summary._STATE, telemetry, transcript, now=moment) is False
    assert len(calls) == 1
    assert summary._refresh_if_due(summary._STATE, telemetry, transcript, now=60.0) is True
    assert len(calls) == 2


# --- threading ------------------------------------------------------------


def test_background_thread_starts_once_and_is_a_daemon(tmp_path, monkeypatch):
    telemetry, transcript = _fixture_tree(tmp_path)
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    _install_transport(monkeypatch, _ok())
    started = []

    def fake_loop(*args):
        started.append(args)

    monkeypatch.setattr(summary, "_loop", fake_loop)

    summary.start_background_refresh(telemetry, transcript)
    summary.start_background_refresh(telemetry, transcript)
    thread = summary._THREAD
    assert thread is not None
    assert thread.daemon is True
    assert thread.name == "stage-summary"
    thread.join(timeout=2)
    assert started == [(telemetry, transcript)]


def test_cached_story_never_raises_on_garbage_state():
    """stream_snapshot() calls this every poll; it must never raise and never block."""
    for garbage in (12345, "   ", "", None, {"not": "text"}, b"bytes"):
        with summary._LOCK:
            summary._CACHE["text"] = garbage
        assert summary.cached_story() is None


def test_cached_story_shape_and_isolation(tmp_path, monkeypatch):
    telemetry, transcript = _fixture_tree(tmp_path)
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    monkeypatch.setenv("STAGE_SUMMARY_MODEL", "vendor/narrator-1")
    _install_transport(monkeypatch, _ok("A recap."))
    summary._refresh_if_due(summary._STATE, telemetry, transcript, now=0.0)
    story = summary.cached_story()
    assert set(story) == {"text", "generated_at", "model"}
    assert story["model"] == "vendor/narrator-1"
    story["text"] = "mutated"
    assert summary.cached_story()["text"] == "A recap."
