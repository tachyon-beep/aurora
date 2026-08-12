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
