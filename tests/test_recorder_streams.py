import json

import pytest

import recorder_streams as rs


def _write_console(tmp_path, data):
    path = tmp_path / "console.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_missing_console_is_empty_not_an_error(tmp_path):
    declarations, error = rs.load_console(str(tmp_path / "absent.json"))
    assert declarations == {}
    assert error is None


def test_torn_console_is_an_error_not_empty(tmp_path):
    path = tmp_path / "console.json"
    path.write_text('{"streams": {"aux"', encoding="utf-8")
    declarations, error = rs.load_console(str(path))
    assert declarations is None
    assert error == "console is not valid json"


def test_wrong_typed_streams_value_is_an_error(tmp_path):
    path = _write_console(tmp_path, {"streams": []})
    declarations, error = rs.load_console(path)
    assert declarations is None
    assert error == "streams is not an object"


def test_console_without_streams_key_is_empty(tmp_path):
    path = _write_console(tmp_path, {})
    declarations, error = rs.load_console(path)
    assert declarations == {}
    assert error is None


def test_a_minimal_declaration_is_accepted_with_no_settings():
    settings, reason = rs.validate_declaration("aux", {})
    assert settings == {}
    assert reason is None


def test_every_field_is_accepted_at_its_boundaries():
    settings, reason = rs.validate_declaration(
        "aux",
        {
            "budget": 0,
            "model": "m",
            "reasoning_effort": "none",
            "temperature": 2,
            "top_p": 0,
            "max_tokens": 1,
        },
    )
    assert reason is None
    assert settings["budget"] == 0
    assert settings["max_tokens"] == 1


@pytest.mark.parametrize(
    "declaration,phrase",
    [
        ({"budget": -1}, "budget"),
        ({"budget": True}, "budget"),
        ({"model": ""}, "model"),
        ({"model": "x" * 201}, "model"),
        ({"reasoning_effort": "max"}, "reasoning_effort"),
        ({"temperature": 2.1}, "temperature"),
        ({"top_p": -0.1}, "top_p"),
        ({"max_tokens": 0}, "max_tokens"),
        ({"max_tokens": 2.5}, "max_tokens"),
        ({"tools": []}, "unknown field: tools"),
    ],
)
def test_bad_values_reject_the_whole_declaration(declaration, phrase):
    settings, reason = rs.validate_declaration("aux", declaration)
    assert settings is None
    assert phrase in reason


@pytest.mark.parametrize("name", ["core", "Aux", "-aux", "a/x", "a.sock", "", "a" * 33])
def test_bad_and_reserved_names_are_rejected(name):
    settings, reason = rs.validate_declaration(name, {})
    assert settings is None
    assert reason in ("invalid stream name", "reserved name")


def test_non_object_declaration_is_rejected():
    settings, reason = rs.validate_declaration("aux", "fast")
    assert settings is None
    assert reason == "declaration is not an object"


def test_evaluate_console_splits_in_file_order():
    accepted, rejected = rs.evaluate_console({"aux": {}, "Bad": {}, "second": {"budget": 3}})
    assert list(accepted) == ["aux", "second"]
    assert rejected == {"Bad": "invalid stream name"}


def test_evaluate_console_enforces_the_stream_cap():
    declarations = {f"s{i}": {} for i in range(10)}
    accepted, rejected = rs.evaluate_console(declarations)
    assert len(accepted) == rs.MAX_STREAMS
    assert rejected == {"s8": "stream limit reached", "s9": "stream limit reached"}


def test_evaluate_console_caps_reported_junk_names():
    accepted, rejected = rs.evaluate_console({"A" * 300: {}})
    assert not accepted
    (name,) = rejected
    assert len(name) <= 80


def test_stream_limit_max_reads_the_environment(monkeypatch):
    monkeypatch.delenv("STREAM_HOURLY_MAX", raising=False)
    assert rs.stream_limit_max() == 120
    monkeypatch.setenv("STREAM_HOURLY_MAX", "5")
    assert rs.stream_limit_max() == 5
    monkeypatch.setenv("STREAM_HOURLY_MAX", "-3")
    assert rs.stream_limit_max() == 0
    monkeypatch.setenv("STREAM_HOURLY_MAX", "many")
    assert rs.stream_limit_max() == 120


def test_effective_allowance_clamps_to_the_ceiling(monkeypatch):
    monkeypatch.setenv("STREAM_HOURLY_MAX", "7")
    assert rs.effective_allowance({"budget": 500}) == 7
    assert rs.effective_allowance({"budget": 3}) == 3
    assert rs.effective_allowance({}) == 7


def test_effective_allowance_defaults_when_undeclared(monkeypatch):
    monkeypatch.delenv("STREAM_HOURLY_MAX", raising=False)
    assert rs.effective_allowance({}) == rs.DEFAULT_STREAM_BUDGET


def test_budget_status_prunes_and_counts_down():
    now = 10_000.0
    status = rs.budget_status([now - 4000, now - 1000, now - 10], now)
    assert status["used"] == 2
    assert status["window_seconds"] == 3600
    assert status["oldest_expires_in_seconds"] == 2600


def test_budget_status_on_an_empty_history():
    assert rs.budget_status([], 10_000.0) == {
        "used": 0,
        "window_seconds": 3600,
        "oldest_expires_in_seconds": None,
    }


def test_check_budget_allows_under_and_refuses_at_allowance():
    now = 10_000.0
    allowed, history = rs.check_budget([], now, 1)
    assert allowed and history == [now]
    allowed, history = rs.check_budget(history, now + 1, 1)
    assert not allowed and history == [now]
    allowed, history = rs.check_budget(history, now + 3601, 1)
    assert allowed and history == [now + 3601]


def test_zero_allowance_refuses_without_a_countdown():
    message = rs.rate_limited_message(0, [], 10_000.0)
    assert message == "rate limited: at most 0 request(s) per hour on this socket"


def test_the_refusal_carries_a_countdown_when_one_exists():
    now = 10_000.0
    message = rs.rate_limited_message(1, [now - 1000], now)
    assert message == (
        "rate limited: at most 1 request(s) per hour on this socket; next available in 2600 seconds"
    )
