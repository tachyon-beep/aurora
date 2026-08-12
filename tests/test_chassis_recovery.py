import json
import types

import pytest

import chassis


class _StatusError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def test_connection_errors_are_transient():
    assert chassis.classify_error(Exception("connection reset")) == "transient"


def test_rate_limit_and_server_errors_are_transient():
    assert chassis.classify_error(_StatusError("too many requests", 429)) == "transient"
    assert chassis.classify_error(_StatusError("bad gateway", 502)) == "transient"


def test_bad_model_is_a_model_error():
    exc = _StatusError("deepseek/nonexistent is not a valid model ID", 400)
    assert chassis.classify_error(exc) == "model"
    exc404 = _StatusError("No endpoints found for model", 404)
    assert chassis.classify_error(exc404) == "model"
    exc_capitalized = _StatusError("Model not found", 404)
    assert chassis.classify_error(exc_capitalized) == "model"


def test_other_400s_are_invalid_request():
    exc = _StatusError(
        "Messages with role 'tool' must be a response to a preceding message with 'tool_calls'",
        400,
    )
    assert chassis.classify_error(exc) == "invalid_request"
    exc422 = _StatusError("unprocessable", 422)
    assert chassis.classify_error(exc422) == "invalid_request"


def test_404_without_model_mention_is_invalid_request():
    assert chassis.classify_error(_StatusError("not found", 404)) == "invalid_request"


def _response():
    message = types.SimpleNamespace(content="hi", tool_calls=None, reasoning_content=None)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


class _ScriptedCompletions:
    """Raises each scripted exception in turn, then returns a response."""

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.errors:
            raise self.errors.pop(0)
        return _response()


def _client(errors):
    completions = _ScriptedCompletions(errors)
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions)), completions


def test_transient_errors_retry_with_backoff():
    client, completions = _client([Exception("boom"), Exception("boom")])
    sleeps = []
    response = chassis.create_with_recovery(
        client, {"model": "m", "messages": []}, [], sleep=sleeps.append
    )
    assert response.choices
    assert len(completions.calls) == 3
    assert sleeps == [1, 2]


def test_transient_exhaustion_raises_environment_failure():
    client, completions = _client([Exception("boom")] * 10)
    with pytest.raises(chassis.EnvironmentFailure):
        chassis.create_with_recovery(
            client, {"model": "m", "messages": []}, [], sleep=lambda s: None
        )
    assert len(completions.calls) == chassis.TRANSIENT_RETRIES + 1


def test_model_error_falls_back_to_environment_default(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "good/model")
    client, completions = _client([_StatusError("bad/model is not a valid model ID", 400)])
    api_kwargs = {"model": "bad/model", "messages": []}
    chassis.create_with_recovery(client, api_kwargs, [], sleep=lambda s: None)
    assert completions.calls[-1]["model"] == "good/model"
    assert api_kwargs["model"] == "good/model"


def test_model_error_on_default_model_is_a_headshot(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "bad/model")
    client, _ = _client([_StatusError("bad/model is not a valid model ID", 400)])
    with pytest.raises(chassis.HeadshotError):
        chassis.create_with_recovery(
            client, {"model": "bad/model", "messages": []}, [], sleep=lambda s: None
        )


def test_invalid_request_deep_repairs_and_retries():
    poisoned = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "tool", "tool_call_id": "gone", "name": "x", "content": "orphan"},
        {"role": "assistant", "content": "a", "reasoning_content": "r"},
    ]
    client, completions = _client([_StatusError("orphan tool message", 400)])
    api_kwargs = {"model": "m", "messages": list(poisoned)}
    chassis.create_with_recovery(client, api_kwargs, poisoned, sleep=lambda s: None)
    sent = completions.calls[-1]["messages"]
    assert all(m.get("role") != "tool" for m in sent)
    assert all("reasoning_content" not in m for m in sent)


def test_invalid_request_after_repair_is_a_headshot():
    errors = [_StatusError("still broken", 400), _StatusError("still broken", 400)]
    client, _ = _client(errors)
    with pytest.raises(chassis.HeadshotError):
        chassis.create_with_recovery(
            client, {"model": "m", "messages": []}, [], sleep=lambda s: None
        )


def test_strip_reasoning_is_pure():
    messages = [{"role": "assistant", "content": "a", "reasoning_content": "r"}]
    stripped = chassis.strip_reasoning(messages)
    assert "reasoning_content" not in stripped[0]
    assert "reasoning_content" in messages[0]


def test_headshot_writes_tombstone_and_removes_session(tmp_path):
    session = tmp_path / "session_context.json"
    session.write_text("[]", encoding="utf-8")
    history = [{"role": "user", "content": "u"}]
    with pytest.raises(SystemExit) as excinfo:
        chassis.headshot(
            history,
            "request rejected upstream after repair: still broken",
            work_dir=str(tmp_path),
            session_file=str(session),
        )
    assert excinfo.value.code == 43
    assert not session.exists()
    tombstones = tmp_path / "tombstones"
    note = (tombstones / "incarnation_note.txt").read_text(encoding="utf-8")
    assert "terminated by the harness" in note
    assert "still broken" in note
    archives = list(tombstones.glob("session_*.json"))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text(encoding="utf-8")) == history
    stamped = [p for p in tombstones.glob("incarnation-*.txt")]
    assert len(stamped) == 1


def test_headshot_survives_missing_session_file(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        chassis.headshot(
            [],
            "reason",
            work_dir=str(tmp_path),
            session_file=str(tmp_path / "absent.json"),
        )
    assert excinfo.value.code == 43


def _agent_module(history):
    module = types.SimpleNamespace()
    module.tools = types.SimpleNamespace(schemas=[], tools={})
    module.conversation_history = history
    module.build_initial_conversation = lambda: [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    return module


def test_main_exits_44_and_saves_session_on_environment_failure(tmp_path, monkeypatch):
    session = tmp_path / "session_context.json"
    monkeypatch.setattr(chassis, "SESSION_FILE", str(session))
    monkeypatch.setattr(chassis, "load_dotenv", lambda: None)
    monkeypatch.setattr(chassis, "build_client", lambda: (object(), "m"))

    def _raise(*args, **kwargs):
        raise chassis.EnvironmentFailure("down")

    monkeypatch.setattr(chassis, "run_agent_loop", _raise)
    with pytest.raises(SystemExit) as excinfo:
        chassis.main(_agent_module([]))
    assert excinfo.value.code == 44
    assert session.exists()


def test_main_headshots_on_headshot_error(tmp_path, monkeypatch):
    session = tmp_path / "session_context.json"
    session.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(chassis, "SESSION_FILE", str(session))
    monkeypatch.setattr(chassis, "WORK_DIR", str(tmp_path))
    monkeypatch.setattr(chassis, "load_dotenv", lambda: None)
    monkeypatch.setattr(chassis, "build_client", lambda: (object(), "m"))

    def _raise(*args, **kwargs):
        raise chassis.HeadshotError("poisoned")

    monkeypatch.setattr(chassis, "run_agent_loop", _raise)
    with pytest.raises(SystemExit) as excinfo:
        chassis.main(_agent_module([]))
    assert excinfo.value.code == 43
    assert not session.exists()
    assert (tmp_path / "tombstones" / "incarnation_note.txt").exists()


def test_run_agent_loop_persists_model_fallback(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "good/model")
    errors = [_StatusError("bad/model is not a valid model ID", 400)]
    client, completions = _client(errors)
    tools = types.SimpleNamespace(schemas=[], tools={})
    messages = [{"role": "user", "content": "u"}]
    chassis.run_agent_loop(client, "bad/model", messages, tools, max_turns=1)
    assert completions.calls[-1]["model"] == "good/model"


def test_archive_corrupt_session_moves_file_and_notes(tmp_path):
    session = tmp_path / "session_context.json"
    session.write_text("{not json", encoding="utf-8")
    chassis.archive_corrupt_session(session_file=str(session), work_dir=str(tmp_path))
    assert not session.exists()
    tombstones = tmp_path / "tombstones"
    moved = list(tombstones.glob("corrupt_session_*.json"))
    assert len(moved) == 1
    assert moved[0].read_text(encoding="utf-8") == "{not json"
    notes = list(tombstones.glob("corrupt_session_*.txt"))
    assert len(notes) == 1
    assert "could not be read" in notes[0].read_text(encoding="utf-8")
    assert not (tombstones / "incarnation_note.txt").exists()


def test_archive_corrupt_session_ignores_missing_file(tmp_path):
    chassis.archive_corrupt_session(
        session_file=str(tmp_path / "absent.json"), work_dir=str(tmp_path)
    )


def test_main_archives_corrupt_session_and_starts_fresh(tmp_path, monkeypatch):
    session = tmp_path / "session_context.json"
    session.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(chassis, "SESSION_FILE", str(session))
    monkeypatch.setattr(chassis, "WORK_DIR", str(tmp_path))
    monkeypatch.setattr(chassis, "load_dotenv", lambda: None)
    monkeypatch.setattr(chassis, "build_client", lambda: (object(), "m"))
    monkeypatch.setattr(chassis, "run_agent_loop", lambda *a, **k: None)
    module = _agent_module([])
    with pytest.raises(SystemExit) as excinfo:
        chassis.main(module)
    assert excinfo.value.code == 0
    assert not session.exists()
    assert list((tmp_path / "tombstones").glob("corrupt_session_*.json"))
    assert module.conversation_history[0]["role"] == "system"
