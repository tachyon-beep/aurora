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


def test_compose_replaces_declared_fields_and_preserves_the_rest():
    body = json.dumps(
        {"model": "sent", "messages": [{"role": "user", "content": "q"}], "temperature": 1.5}
    ).encode("utf-8")
    composed, error = rs.compose_body(
        body, {"model": "declared", "reasoning_effort": "low", "budget": 3}
    )
    assert error is None
    data = json.loads(composed.decode("utf-8"))
    assert data["model"] == "declared"
    assert data["reasoning_effort"] == "low"
    assert data["temperature"] == 1.5
    assert data["messages"] == [{"role": "user", "content": "q"}]
    assert "budget" not in data


def test_compose_with_no_settings_round_trips_the_object():
    body = json.dumps({"model": "m", "messages": []}).encode("utf-8")
    composed, error = rs.compose_body(body, {})
    assert error is None
    assert json.loads(composed.decode("utf-8")) == {"model": "m", "messages": []}


def test_compose_refuses_a_non_object_body():
    for body in (b"[]", b"not json", b"\xff\xfe"):
        composed, error = rs.compose_body(body, {"model": "m"})
        assert composed is None
        assert error == "request body is not a json object"


def test_render_state_reports_core_and_each_stream(monkeypatch):
    monkeypatch.setenv("STREAM_HOURLY_MAX", "7")
    now = 10_000.0
    state = rs.render_state(
        {"aux": {"budget": 500, "model": "m"}},
        {"Bad": "invalid stream name"},
        {"aux": [now - 100]},
        now,
    )
    streams = state["streams"]
    assert streams["core"] == {"socket": "core.sock", "status": "active"}
    aux = streams["aux"]
    assert aux["socket"] == "aux.sock"
    assert aux["status"] == "active"
    assert aux["settings"] == {"model": "m"}
    assert aux["budget"]["allowance"] == 7
    assert aux["budget"]["used"] == 1
    assert aux["budget"]["oldest_expires_in_seconds"] == 3500
    assert streams["Bad"] == {"status": "rejected", "reason": "invalid stream name"}
    assert "console_error" not in state


def test_render_state_carries_a_console_error_only_when_given():
    state = rs.render_state({}, {}, {}, 0.0, console_error="console is not valid json")
    assert state["console_error"] == "console is not valid json"


def test_write_state_is_atomic_and_readable(tmp_path):
    path = str(tmp_path / "streams.json")
    rs.write_state(path, {"streams": {}})
    assert json.loads((tmp_path / "streams.json").read_text(encoding="utf-8")) == {"streams": {}}
    assert list(tmp_path.iterdir()) == [tmp_path / "streams.json"]


def test_readme_names_the_protocol_and_stays_affectless(tmp_path):
    rs.write_readme(str(tmp_path))
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    for phrase in (
        "core.sock",
        "/llm/console/console.json",
        "streams:",
        "budget:",
        "reasoning_effort:",
        "streams.json",
        "POST",
    ):
        assert phrase in text
    assert "!" not in text


def _registry(now=10_000.0):
    return rs.StreamRegistry(clock=lambda: now)


def test_apply_reports_added_and_removed():
    registry = _registry()
    added, removed = registry.apply({"aux": {}}, {})
    assert added == ["aux"] and removed == []
    added, removed = registry.apply({"other": {}}, {})
    assert added == ["other"] and removed == ["aux"]


def test_admit_composes_and_charges(monkeypatch):
    monkeypatch.delenv("STREAM_HOURLY_MAX", raising=False)
    registry = _registry()
    registry.apply({"aux": {"model": "declared", "budget": 1}}, {})
    body = json.dumps({"model": "sent", "messages": []}).encode("utf-8")
    composed, refusal = registry.admit("aux", body)
    assert refusal is None
    assert json.loads(composed.decode("utf-8"))["model"] == "declared"
    composed, refusal = registry.admit("aux", body)
    assert composed == body
    status, message = refusal
    assert status == 429
    assert message.startswith("rate limited: at most 1 request(s) per hour")
    assert "next available in" in message


def test_admit_refuses_a_bad_body_without_charging():
    registry = _registry()
    registry.apply({"aux": {"budget": 1}}, {})
    body, refusal = registry.admit("aux", b"not json")
    assert refusal == (400, "request body is not a json object")
    _, refusal = registry.admit("aux", json.dumps({"messages": []}).encode("utf-8"))
    assert refusal is None


def test_admit_on_an_unknown_stream():
    registry = _registry()
    body, refusal = registry.admit("gone", b"{}")
    assert refusal == (503, "stream not available")


def test_removed_streams_forget_their_histories():
    registry = _registry()
    registry.apply({"aux": {"budget": 1}}, {})
    registry.admit("aux", b"{}")
    registry.apply({}, {})
    registry.apply({"aux": {"budget": 1}}, {})
    _, refusal = registry.admit("aux", b"{}")
    assert refusal is None


def test_reject_moves_a_stream_into_the_rejected_set():
    registry = _registry()
    registry.apply({"aux": {}}, {})
    registry.reject("aux", "bind failed: OSError")
    state = registry.state()
    assert state["streams"]["aux"] == {"status": "rejected", "reason": "bind failed: OSError"}


def test_state_reflects_use_and_console_errors():
    registry = _registry()
    registry.apply({"aux": {"budget": 5}}, {})
    registry.admit("aux", b"{}")
    state = registry.state(console_error=None)
    assert state["streams"]["aux"]["budget"]["used"] == 1
    state = registry.state(console_error="console is not valid json")
    assert state["console_error"] == "console is not valid json"
