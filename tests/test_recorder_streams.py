import io
import json

import pytest

import recorder_streams as rs


def _write_console(tmp_path, data):
    path = tmp_path / "console.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


@pytest.fixture(autouse=True)
def stream_env(monkeypatch):
    """A recorder with its stream credential present and no models permitted.

    Tests that exercise the credential gate delete the key again themselves.
    """
    for var in ("STREAM_MODEL_ALLOW_TEXT", "STREAM_MODEL_ALLOW_VISION", "STREAM_UPSTREAM_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-stream-test")
    return monkeypatch


def test_missing_console_is_empty_not_an_error(tmp_path):
    declarations, enabled, error = rs.load_console(str(tmp_path / "absent.json"))
    assert declarations == {}
    assert enabled is False
    assert error is None


def test_torn_console_is_an_error_not_empty(tmp_path):
    path = tmp_path / "console.json"
    path.write_text('{"streams": {"aux"', encoding="utf-8")
    declarations, enabled, error = rs.load_console(str(path))
    assert declarations is None
    assert enabled is False
    assert error == "console is not valid json"


def test_wrong_typed_streams_value_is_an_error(tmp_path):
    path = _write_console(tmp_path, {"streams": []})
    declarations, enabled, error = rs.load_console(path)
    assert declarations is None
    assert enabled is False
    assert error == "streams is not an object"


def test_console_without_streams_key_is_empty(tmp_path):
    path = _write_console(tmp_path, {})
    declarations, enabled, error = rs.load_console(path)
    assert declarations == {}
    assert enabled is False
    assert error is None


@pytest.mark.parametrize(
    "enable_streams,expected",
    [
        (True, True),
        (False, False),
        ("true", False),
        (1, False),
        (None, False),
    ],
)
def test_enable_streams_requires_the_literal_json_boolean(tmp_path, enable_streams, expected):
    path = _write_console(tmp_path, {"enable_streams": enable_streams, "streams": {}})
    declarations, enabled, error = rs.load_console(path)
    assert declarations == {}
    assert enabled is expected
    assert error is None


def test_enable_streams_absent_is_disabled(tmp_path):
    path = _write_console(tmp_path, {"streams": {"aux": {}}})
    declarations, enabled, error = rs.load_console(path)
    assert declarations == {"aux": {}}
    assert enabled is False
    assert error is None


def test_console_parser_reads_only_bounded_input(monkeypatch):
    class ReadProbe(io.BytesIO):
        def __init__(self):
            super().__init__(b'{"streams": {}}' + b" " * rs.CONSOLE_MAX_BYTES)
            self.sizes = []

        def read(self, size=-1):
            self.sizes.append(size)
            return super().read(size)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    probe = ReadProbe()
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: probe)

    declarations, enabled, error = rs.load_console("agent-controlled.json")

    assert probe.sizes == [rs.CONSOLE_MAX_BYTES + 1]
    assert declarations is None
    assert enabled is False
    assert error == "console is too large"


def test_a_minimal_declaration_is_accepted_with_no_settings():
    settings, reason = rs.validate_declaration("aux", {})
    assert settings == {}
    assert reason is None


def test_every_field_is_accepted_at_its_boundaries(monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "m")
    settings, reason = rs.validate_declaration(
        "aux",
        {
            "budget": 0,
            "token_budget": 0,
            "model": "m",
            "reasoning_effort": "none",
            "temperature": 2,
            "top_p": 0,
            "max_tokens": 1,
        },
    )
    assert reason is None
    assert settings["budget"] == 0
    assert settings["token_budget"] == 0
    assert settings["max_tokens"] == 1


@pytest.mark.parametrize(
    "declaration,phrase",
    [
        ({"budget": -1}, "budget"),
        ({"budget": True}, "budget"),
        ({"token_budget": -1}, "token_budget must be an integer of at least 0"),
        ({"token_budget": True}, "token_budget must be an integer of at least 0"),
        ({"token_budget": 1.5}, "token_budget must be an integer of at least 0"),
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


def test_an_unknown_field_is_still_rejected_beside_the_budget_fields():
    settings, reason = rs.validate_declaration(
        "aux", {"budget": 1, "token_budget": 1, "upstream": "elsewhere"}
    )
    assert settings is None
    assert reason == "unknown field: upstream"


@pytest.mark.parametrize("name", ["core", "Aux", "-aux", "a/x", "a.sock", "", "a" * 33])
def test_bad_and_reserved_names_are_rejected(name):
    settings, reason = rs.validate_declaration(name, {})
    assert settings is None
    assert reason in ("invalid stream name", "reserved name")


def test_non_object_declaration_is_rejected():
    settings, reason = rs.validate_declaration("aux", "fast")
    assert settings is None
    assert reason == "declaration is not an object"


def test_permitted_models_parses_the_environment(monkeypatch):
    assert rs.permitted_models() == []
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "")
    assert rs.permitted_models() == []
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "a/b")
    assert rs.permitted_models() == ["a/b"]
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", " a/b , c-d ,, ")
    assert rs.permitted_models() == ["a/b", "c-d"]


def test_permitted_models_unions_the_text_and_vision_lists(monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "a/b,c")
    monkeypatch.setenv("STREAM_MODEL_ALLOW_VISION", "v/x, a/b")
    assert rs.permitted_models() == ["a/b", "c", "v/x"]


def test_permitted_models_is_empty_without_the_stream_credential(monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "a/b")
    monkeypatch.setenv("STREAM_MODEL_ALLOW_VISION", "v/x")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert rs.permitted_models() == []
    assert rs.model_catalog() == []


def test_model_catalog_marks_vision_entries(monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "a/b")
    monkeypatch.setenv("STREAM_MODEL_ALLOW_VISION", "v/x")
    assert rs.model_catalog() == [
        {"id": "a/b", "image_input": False},
        {"id": "v/x", "image_input": True},
    ]


def test_a_declared_model_is_rejected_when_none_are_permitted():
    settings, reason = rs.validate_declaration("aux", {"model": "any"})
    assert settings is None
    assert reason == "model not permitted"


def test_a_declaration_without_a_model_needs_no_allow_list():
    settings, reason = rs.validate_declaration("aux", {"budget": 2, "temperature": 1})
    assert reason is None
    assert settings == {"budget": 2, "temperature": 1}


def test_a_permitted_model_is_accepted(monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "a/b,c")
    settings, reason = rs.validate_declaration("aux", {"model": "a/b", "budget": 1})
    assert reason is None
    assert settings == {"model": "a/b", "budget": 1}


def test_a_vision_model_is_accepted_like_any_other(monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_VISION", "v/x")
    settings, reason = rs.validate_declaration("aux", {"model": "v/x"})
    assert reason is None
    assert settings == {"model": "v/x"}


@pytest.mark.parametrize("model", ["a/B", " a/b", "a/b ", "d"])
def test_a_model_outside_the_list_rejects_the_whole_declaration(monkeypatch, model):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "a/b,c")
    settings, reason = rs.validate_declaration("aux", {"model": model, "budget": 1})
    assert settings is None
    assert reason == "model not permitted"


def test_model_shape_errors_precede_the_allow_list(monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "a")
    settings, reason = rs.validate_declaration("aux", {"model": ""})
    assert settings is None
    assert "model must be a non-empty string" in reason


def test_minimal_reasoning_effort_is_declarable():
    settings, reason = rs.validate_declaration("aux", {"reasoning_effort": "minimal"})
    assert reason is None
    assert settings == {"reasoning_effort": "minimal"}


def test_a_model_rejection_is_reported_like_other_validation_errors(monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "a")
    accepted, rejected = rs.evaluate_console({"aux": {"model": "b"}, "ok": {"model": "a"}}, True)
    assert list(accepted) == ["ok"]
    assert rejected == {"aux": "model not permitted"}
    state = rs.render_state(accepted, rejected, {}, 0.0, True)
    assert state["streams"]["aux"] == {"status": "rejected", "reason": "model not permitted"}
    assert state["streams"]["ok"]["status"] == "active"


def test_evaluate_console_splits_in_file_order():
    accepted, rejected = rs.evaluate_console({"aux": {}, "Bad": {}, "second": {"budget": 3}}, True)
    assert list(accepted) == ["aux", "second"]
    assert rejected == {"Bad": "invalid stream name"}


def test_every_declaration_is_rejected_without_the_stream_credential(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    accepted, rejected = rs.evaluate_console({"aux": {}, "other": {"budget": 1}}, True)
    assert accepted == {}
    assert rejected == {
        "aux": "streams are not available",
        "other": "streams are not available",
    }


def test_the_disabled_gate_reports_ahead_of_the_missing_credential(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    accepted, rejected = rs.evaluate_console({"aux": {}}, False)
    assert accepted == {}
    assert rejected == {"aux": "streams are not enabled"}


def test_evaluate_console_enforces_the_stream_cap():
    declarations = {f"s{i}": {} for i in range(10)}
    accepted, rejected = rs.evaluate_console(declarations, True)
    assert len(accepted) == rs.MAX_STREAMS
    assert rejected == {"s8": "stream limit reached", "s9": "stream limit reached"}


def test_evaluate_console_caps_reported_junk_names():
    accepted, rejected = rs.evaluate_console({"A" * 300: {}}, True)
    assert not accepted
    (name,) = rejected
    assert len(name) <= 80


def test_evaluate_console_rejects_everything_when_the_gate_is_off():
    accepted, rejected = rs.evaluate_console({"aux": {}, "Bad": {}}, False)
    assert accepted == {}
    assert rejected == {"aux": "streams are not enabled", "Bad": "streams are not enabled"}


def test_evaluate_console_gate_rejection_beats_the_stream_cap():
    declarations = {f"s{i}": {} for i in range(9)}
    accepted, rejected = rs.evaluate_console(declarations, False)
    assert accepted == {}
    assert rejected == {name: "streams are not enabled" for name in declarations}
    assert "stream limit reached" not in rejected.values()


def test_evaluate_console_gate_off_still_caps_reported_junk_names():
    accepted, rejected = rs.evaluate_console({"A" * 300: {}}, False)
    assert not accepted
    (name,) = rejected
    assert len(name) <= 80
    assert rejected[name] == "streams are not enabled"


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


def test_stream_token_limit_max_reads_the_environment(monkeypatch):
    monkeypatch.delenv("STREAM_TOKEN_HOURLY_MAX", raising=False)
    assert rs.stream_token_limit_max() == 2_000_000
    monkeypatch.setenv("STREAM_TOKEN_HOURLY_MAX", "5000")
    assert rs.stream_token_limit_max() == 5000
    monkeypatch.setenv("STREAM_TOKEN_HOURLY_MAX", "-3")
    assert rs.stream_token_limit_max() == 0
    monkeypatch.setenv("STREAM_TOKEN_HOURLY_MAX", "many")
    assert rs.stream_token_limit_max() == 2_000_000
    monkeypatch.setenv("STREAM_TOKEN_HOURLY_MAX", "")
    assert rs.stream_token_limit_max() == 2_000_000
    monkeypatch.setenv("STREAM_TOKEN_HOURLY_MAX", "9000000")
    assert rs.stream_token_limit_max() == 9_000_000


def test_effective_token_allowance_clamps_to_the_ceiling(monkeypatch):
    monkeypatch.setenv("STREAM_TOKEN_HOURLY_MAX", "500")
    assert rs.effective_token_allowance({"token_budget": 5_000_000}) == 500
    assert rs.effective_token_allowance({"token_budget": 20}) == 20


def test_effective_token_allowance_defaults_to_the_ceiling(monkeypatch):
    monkeypatch.delenv("STREAM_TOKEN_HOURLY_MAX", raising=False)
    assert rs.effective_token_allowance({}) == 2_000_000
    monkeypatch.setenv("STREAM_TOKEN_HOURLY_MAX", "500000")
    assert rs.effective_token_allowance({}) == 500_000
    assert rs.effective_token_allowance({"budget": 3}) == 500_000


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


def test_token_status_sums_the_tokens_in_the_window():
    now = 10_000.0
    status = rs.token_status([(now - 4000, 900), (now - 1000, 100), (now - 10, 200)], now)
    assert status["used"] == 300
    assert status["window_seconds"] == 3600
    assert status["oldest_expires_in_seconds"] == 2600


def test_token_status_on_an_empty_history():
    assert rs.token_status([], 10_000.0) == {
        "used": 0,
        "window_seconds": 3600,
        "oldest_expires_in_seconds": None,
    }


def test_check_token_budget_allows_under_and_refuses_at_allowance():
    now = 10_000.0
    allowed, history = rs.check_token_budget([], now, 100)
    assert allowed and history == []
    allowed, history = rs.check_token_budget([(now, 100)], now + 1, 100)
    assert not allowed and history == [(now, 100)]
    allowed, history = rs.check_token_budget([(now, 100)], now + 3601, 100)
    assert allowed and history == []


def test_check_token_budget_refuses_a_zero_allowance():
    allowed, history = rs.check_token_budget([], 10_000.0, 0)
    assert not allowed and history == []


def test_the_token_refusal_names_tokens_and_carries_a_countdown():
    now = 10_000.0
    assert rs.token_limited_message(500, [], now) == (
        "rate limited: at most 500 token(s) per hour on this socket"
    )
    message = rs.token_limited_message(500, [(now - 1000, 500)], now)
    assert message == (
        "rate limited: at most 500 token(s) per hour on this socket; next available in 2600 seconds"
    )
    assert message != rs.rate_limited_message(500, [now - 1000], now)


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
        True,
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


def test_render_state_reports_the_token_window(monkeypatch):
    monkeypatch.setenv("STREAM_TOKEN_HOURLY_MAX", "5000")
    now = 10_000.0
    state = rs.render_state(
        {"aux": {"token_budget": 900}, "quiet": {}},
        {},
        {},
        now,
        True,
        token_histories={"aux": [(now - 7200, 4000), (now - 100, 250), (now - 50, 50)]},
    )
    aux = state["streams"]["aux"]
    assert aux["tokens"] == {
        "allowance": 900,
        "used": 300,
        "window_seconds": 3600,
        "oldest_expires_in_seconds": 3500,
    }
    assert "token_budget" not in aux["settings"]
    assert state["streams"]["quiet"]["tokens"] == {
        "allowance": 5000,
        "used": 0,
        "window_seconds": 3600,
        "oldest_expires_in_seconds": None,
    }


def test_render_state_carries_a_console_error_only_when_given():
    state = rs.render_state({}, {}, {}, 0.0, True, console_error="console is not valid json")
    assert state["console_error"] == "console is not valid json"


def test_render_state_reports_streams_enabled_in_both_states():
    state = rs.render_state({}, {}, {}, 0.0, True)
    assert state["streams_enabled"] is True
    state = rs.render_state({}, {}, {}, 0.0, False)
    assert state["streams_enabled"] is False


def test_write_state_is_atomic_and_readable(tmp_path):
    path = str(tmp_path / "streams.json")
    rs.write_state(path, {"streams": {}})
    assert json.loads((tmp_path / "streams.json").read_text(encoding="utf-8")) == {"streams": {}}
    assert list(tmp_path.iterdir()) == [tmp_path / "streams.json"]


def test_write_models_lists_the_permitted_identifiers(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "a/b, c")
    monkeypatch.setenv("STREAM_MODEL_ALLOW_VISION", "v/x")
    assert rs.write_models(str(tmp_path)) is True
    document = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    assert document == {
        "models": [
            {"id": "a/b", "image_input": False},
            {"id": "c", "image_input": False},
            {"id": "v/x", "image_input": True},
        ]
    }
    assert list(tmp_path.iterdir()) == [tmp_path / "models.json"]


def test_write_models_is_empty_when_none_are_permitted(tmp_path):
    assert rs.write_models(str(tmp_path)) is True
    document = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    assert document == {"models": []}


def test_write_models_is_empty_without_the_stream_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "a")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert rs.write_models(str(tmp_path)) is True
    document = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    assert document == {"models": []}


def test_write_models_rewrites_only_on_change(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "a")
    assert rs.write_models(str(tmp_path)) is True
    assert rs.write_models(str(tmp_path)) is False
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "a,b")
    assert rs.write_models(str(tmp_path)) is True
    document = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    assert document == {
        "models": [
            {"id": "a", "image_input": False},
            {"id": "b", "image_input": False},
        ]
    }


def test_write_models_removes_a_stray_temporary_on_the_unchanged_path(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "a")
    assert rs.write_models(str(tmp_path)) is True
    (tmp_path / "models.json.tmp").write_text("{}", encoding="utf-8")
    assert rs.write_models(str(tmp_path)) is False
    assert list(tmp_path.iterdir()) == [tmp_path / "models.json"]


def test_write_models_replaces_a_file_it_cannot_read(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "a")
    (tmp_path / "models.json").write_text("not json", encoding="utf-8")
    assert rs.write_models(str(tmp_path)) is True
    assert json.loads((tmp_path / "models.json").read_text(encoding="utf-8")) == {
        "models": [{"id": "a", "image_input": False}]
    }


def test_readme_names_the_protocol_and_stays_affectless(tmp_path):
    rs.write_readme(str(tmp_path))
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    for phrase in (
        "core.sock",
        "/llm/console/console.json",
        "enable_streams:",
        "streams:",
        "budget:",
        "token_budget:",
        "reasoning_effort:",
        "streams.json",
        "the model identifiers a declaration may set are listed in\nmodels.json",
        "POST",
        "is not served unless enable_streams is true",
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
    registry.apply({"aux": {"model": "declared", "budget": 1, "max_tokens": 10}}, {})
    body = json.dumps({"model": "sent", "messages": []}).encode("utf-8")
    composed, refusal, _ = registry.admit("aux", body)
    assert refusal is None
    assert json.loads(composed.decode("utf-8"))["model"] == "declared"
    composed, refusal, _ = registry.admit("aux", body)
    assert composed == body
    status, message = refusal
    assert status == 429
    assert message.startswith("rate limited: at most 1 request(s) per hour")
    assert "next available in" in message


def test_admit_refuses_a_bad_body_without_charging():
    registry = _registry()
    registry.apply({"aux": {"budget": 1}}, {})
    body, refusal, _ = registry.admit("aux", b"not json")
    assert refusal == (400, "request body is not a json object")
    _, refusal, _ = registry.admit("aux", json.dumps({"messages": []}).encode("utf-8"))
    assert refusal is None


def test_admit_on_an_unknown_stream():
    registry = _registry()
    body, refusal, _ = registry.admit("gone", b"{}")
    assert refusal == (503, "stream not available")


def test_a_retired_stream_stays_charged_for_the_rest_of_its_window():
    registry = _registry()
    registry.apply({"aux": {"budget": 1}}, {})
    registry.admit("aux", b"{}")
    registry.apply({}, {})
    registry.apply({"aux": {"budget": 1}}, {})
    _, refusal, _ = registry.admit("aux", b"{}")
    assert refusal is not None
    assert refusal[0] == 429


def test_a_rejected_stream_stays_charged_when_it_is_declared_again():
    registry = _registry()
    registry.apply({"aux": {"budget": 1}}, {})
    registry.admit("aux", b"{}")
    registry.reject("aux", "bind failed: OSError")
    registry.apply({"aux": {"budget": 1}}, {})
    _, refusal, _ = registry.admit("aux", b"{}")
    assert refusal is not None
    assert refusal[0] == 429


def test_a_retired_stream_is_forgotten_once_its_window_has_passed():
    now = [10_000.0]
    registry = rs.StreamRegistry(clock=lambda: now[0])
    registry.apply({"aux": {"budget": 1}}, {})
    registry.admit("aux", b"{}")
    registry.apply({}, {})
    now[0] += rs.BUDGET_WINDOW + 1
    registry.apply({"aux": {"budget": 1}}, {})
    _, refusal, _ = registry.admit("aux", b"{}")
    assert refusal is None


def test_admit_refuses_at_the_token_ceiling():
    registry = _registry()
    registry.apply({"aux": {"budget": 5, "token_budget": 100}}, {})
    _, refusal, _ = registry.admit("aux", b"{}")
    assert refusal is None
    registry.charge("aux", 100)
    body, refusal, _ = registry.admit("aux", b"{}")
    assert body == b"{}"
    status, message = refusal
    assert status == 429
    assert message.startswith("rate limited: at most 100 token(s) per hour")


def test_a_token_refusal_spends_no_request_budget():
    registry = _registry()
    registry.apply({"aux": {"budget": 5, "token_budget": 100}}, {})
    registry.charge("aux", 100)
    _, refusal, _ = registry.admit("aux", b"{}")
    assert refusal[0] == 429
    assert registry.state()["streams"]["aux"]["budget"]["used"] == 0


def test_a_request_refusal_reserves_no_tokens():
    # The token window is read before the request window, so a request refused
    # for rate adds nothing to it. What is already there is the reservation the
    # admitted request holds until it settles.
    registry = _registry()
    registry.apply(
        {"aux": {"budget": 1, "token_budget": 1000, "max_tokens": 10, "reasoning_effort": "none"}},
        {},
    )
    _, _, ticket = registry.admit("aux", b"{}")
    held = registry.state()["streams"]["aux"]["tokens"]["used"]
    assert held > 0

    _, refusal, refused_ticket = registry.admit("aux", b"{}")

    assert "request(s)" in refusal[1]
    assert refused_ticket is None
    assert registry.state()["streams"]["aux"]["tokens"]["used"] == held
    registry.settle("aux", ticket, 0)
    assert registry.state()["streams"]["aux"]["tokens"]["used"] == 0
    registry.apply({}, {})
    assert "aux" not in registry.state()["streams"]


def test_the_token_ceiling_is_reported_when_both_are_spent():
    registry = _registry()
    registry.apply({"aux": {"budget": 1, "token_budget": 100}}, {})
    registry.admit("aux", b"{}")
    registry.charge("aux", 100)
    _, refusal, _ = registry.admit("aux", b"{}")
    assert "token(s)" in refusal[1]


def test_charge_records_tokens_against_the_window():
    registry = _registry()
    registry.apply({"aux": {"token_budget": 1000}}, {})
    registry.charge("aux", 120)
    registry.charge("aux", 30)
    tokens = registry.state()["streams"]["aux"]["tokens"]
    assert tokens["used"] == 150
    assert tokens["allowance"] == 1000
    assert tokens["oldest_expires_in_seconds"] == 3600


def test_charge_ignores_an_amount_that_spends_nothing():
    registry = _registry()
    registry.apply({"aux": {"token_budget": 1000}}, {})
    for amount in (0, -5, True, 2.5, None, "many"):
        registry.charge("aux", amount)
    tokens = registry.state()["streams"]["aux"]["tokens"]
    assert tokens["used"] == 0
    assert tokens["oldest_expires_in_seconds"] is None


def test_a_request_admitted_under_the_ceiling_may_carry_the_window_over_it():
    registry = _registry()
    registry.apply({"aux": {"budget": 5, "token_budget": 1000}}, {})
    registry.charge("aux", 999)
    _, refusal, ticket = registry.admit("aux", b"{}")
    assert refusal is None
    registry.settle("aux", ticket, 5000)
    tokens = registry.state()["streams"]["aux"]["tokens"]
    assert tokens["used"] == 5999
    assert tokens["used"] > tokens["allowance"]
    _, refusal, _ = registry.admit("aux", b"{}")
    assert refusal[0] == 429
    assert "token(s)" in refusal[1]


def test_a_retired_stream_stays_charged_for_tokens_when_it_is_declared_again():
    registry = _registry()
    registry.apply({"aux": {"budget": 5, "token_budget": 100}}, {})
    registry.charge("aux", 100)
    registry.apply({}, {})
    registry.apply({"aux": {"budget": 5, "token_budget": 100}}, {})
    _, refusal, _ = registry.admit("aux", b"{}")
    assert refusal[0] == 429
    assert "token(s)" in refusal[1]


def test_a_retired_stream_token_charge_ages_out_after_the_window():
    now = [10_000.0]
    registry = rs.StreamRegistry(clock=lambda: now[0])
    registry.apply({"aux": {"token_budget": 100}}, {})
    registry.charge("aux", 100)
    registry.apply({}, {})
    now[0] += rs.BUDGET_WINDOW + 1
    registry.apply({"aux": {"token_budget": 100}}, {})
    _, refusal, _ = registry.admit("aux", b"{}")
    assert refusal is None


def test_a_retired_stream_is_absent_from_the_state_document():
    registry = _registry()
    registry.apply({"aux": {"budget": 1}}, {})
    registry.admit("aux", b"{}")
    registry.apply({}, {})
    assert "aux" not in registry.state()["streams"]


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


def test_state_threads_streams_enabled_through_to_the_document():
    registry = _registry()
    state = registry.state(streams_enabled=True)
    assert state["streams_enabled"] is True
    state = registry.state(streams_enabled=False)
    assert state["streams_enabled"] is False


def test_turning_the_gate_off_tears_down_existing_streams():
    registry = _registry()
    declarations = {"aux": {}, "critic": {"budget": 3}}

    accepted, rejected = rs.evaluate_console(declarations, True)
    added, removed = registry.apply(accepted, rejected)
    assert set(added) == {"aux", "critic"}
    assert removed == []
    state = registry.state(streams_enabled=True)
    assert state["streams"]["aux"]["status"] == "active"
    assert state["streams"]["critic"]["status"] == "active"

    accepted, rejected = rs.evaluate_console(declarations, False)
    added, removed = registry.apply(accepted, rejected)
    assert added == []
    assert set(removed) == {"aux", "critic"}
    state = registry.state(streams_enabled=False)
    assert state["streams_enabled"] is False
    assert state["streams"]["aux"] == {
        "status": "rejected",
        "reason": "streams are not enabled",
    }
    assert state["streams"]["critic"] == {
        "status": "rejected",
        "reason": "streams are not enabled",
    }


def _compose(body, settings, allowance):
    composed, error = rs.compose_body(json.dumps(body).encode("utf-8"), settings, allowance)
    assert error is None
    return json.loads(composed.decode("utf-8"))


def test_compose_adds_reasoning_allowance_to_a_capped_request():
    out = _compose({"messages": [], "max_tokens": 400}, {}, 8192)
    assert out["max_tokens"] == 8592


def test_compose_adds_allowance_to_a_declared_cap():
    out = _compose({"messages": []}, {"max_tokens": 400}, 8192)
    assert out["max_tokens"] == 8592


def test_compose_skips_allowance_when_reasoning_is_none():
    out = _compose({"messages": [], "max_tokens": 400}, {"reasoning_effort": "none"}, 8192)
    assert out["max_tokens"] == 400
    out = _compose({"messages": [], "max_tokens": 400, "reasoning_effort": "none"}, {}, 8192)
    assert out["max_tokens"] == 400


def test_compose_skips_allowance_without_a_cap_or_with_zero_allowance():
    out = _compose({"messages": []}, {}, 8192)
    assert "max_tokens" not in out
    out = _compose({"messages": [], "max_tokens": 400}, {}, 0)
    assert out["max_tokens"] == 400


def test_compose_ignores_a_malformed_body_cap():
    out = _compose({"messages": [], "max_tokens": "many"}, {}, 8192)
    assert out["max_tokens"] == "many"
    out = _compose({"messages": [], "max_tokens": True}, {}, 8192)
    assert out["max_tokens"] is True


def test_compose_asks_a_streamed_request_to_report_its_usage():
    out = _compose({"messages": [], "stream": True}, {}, 0)
    assert out["stream_options"] == {"include_usage": True}


def test_compose_keeps_the_stream_options_a_request_already_carries():
    out = _compose(
        {"messages": [], "stream": True, "stream_options": {"include_usage": False, "other": 1}},
        {},
        0,
    )
    assert out["stream_options"] == {"include_usage": True, "other": 1}


def test_compose_replaces_a_malformed_stream_options_value():
    out = _compose({"messages": [], "stream": True, "stream_options": "yes"}, {}, 0)
    assert out["stream_options"] == {"include_usage": True}


@pytest.mark.parametrize("body", [{"messages": []}, {"messages": [], "stream": False}])
def test_compose_leaves_an_unstreamed_request_without_stream_options(body):
    out = _compose(body, {}, 0)
    assert "stream_options" not in out


def test_compose_treats_a_truthy_non_boolean_stream_as_unstreamed():
    out = _compose({"messages": [], "stream": 1}, {}, 0)
    assert "stream_options" not in out


def test_reasoning_allowance_reads_the_environment(monkeypatch):
    monkeypatch.delenv("STREAM_REASONING_ALLOWANCE", raising=False)
    assert rs.reasoning_allowance() == rs.DEFAULT_REASONING_ALLOWANCE
    monkeypatch.setenv("STREAM_REASONING_ALLOWANCE", "1000")
    assert rs.reasoning_allowance() == 1000
    monkeypatch.setenv("STREAM_REASONING_ALLOWANCE", "0")
    assert rs.reasoning_allowance() == 0
    monkeypatch.setenv("STREAM_REASONING_ALLOWANCE", "-5")
    assert rs.reasoning_allowance() == 0
    monkeypatch.setenv("STREAM_REASONING_ALLOWANCE", "junk")
    assert rs.reasoning_allowance() == rs.DEFAULT_REASONING_ALLOWANCE


def _declared(registry, name="text_1", **settings):
    """Adopt one accepted declaration so admit() has something to serve."""
    base = {"model": "m", "budget": 100, "token_budget": 1000, "reasoning_effort": "none"}
    base.update(settings)
    registry.apply({name: base}, {})
    return name


def test_concurrent_admissions_cannot_all_spend_the_same_empty_window(monkeypatch):
    # check_budget reserves a request slot on admission; the token window has to
    # reserve too, or every request in flight sees a window none of the others
    # has charged yet. With keep-alive the agent holds many connections open at
    # once, so this is the ordinary case rather than a race that needs winning.
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "m")
    registry = rs.StreamRegistry()
    name = _declared(registry, token_budget=1000, max_tokens=400)

    body = json.dumps({"model": "m", "messages": []}).encode()
    admitted = 0
    for _ in range(10):
        _, refusal, _ = registry.admit(name, body)
        if refusal is None:
            admitted += 1

    # Each admission may consume up to its composed max_tokens, so a 1000-token
    # allowance must not admit ten 400-token requests before any of them settles.
    assert admitted <= 3


def test_a_settled_request_releases_the_difference_it_did_not_spend(monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "m")
    registry = rs.StreamRegistry()
    name = _declared(registry, token_budget=1000, max_tokens=400)
    body = json.dumps({"model": "m", "messages": []}).encode()

    _, refusal, ticket = registry.admit(name, body)
    assert refusal is None
    held = rs.token_status(registry._token_histories[name], registry._clock())["used"]
    assert held > 10

    registry.settle(name, ticket, 10)

    # The reservation is replaced by what was actually spent, not added to.
    assert rs.token_status(registry._token_histories[name], registry._clock())["used"] == 10


def test_an_unsettled_request_keeps_its_reservation(monkeypatch):
    # A streamed response whose usage event never arrives - a client that
    # disconnected, an upstream that faulted - must not fall back to zero.
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "m")
    registry = rs.StreamRegistry()
    name = _declared(registry, token_budget=1000, max_tokens=400)
    body = json.dumps({"model": "m", "messages": []}).encode()

    _, refusal, ticket = registry.admit(name, body)
    assert refusal is None
    registry.settle(name, ticket, None)

    assert rs.token_status(registry._token_histories[name], registry._clock())["used"] >= 400


def test_a_declaration_without_max_tokens_still_reserves_its_prompt(monkeypatch):
    # compose_body only replaces max_tokens when the declaration carries one, so
    # a stream declared without it would otherwise reserve nothing and a large
    # prompt would be billed upstream while charging zero here.
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "m")
    registry = rs.StreamRegistry()
    name = _declared(registry, token_budget=1000)
    body = json.dumps(
        {"model": "m", "messages": [{"role": "user", "content": "x" * 8000}]}
    ).encode()

    _, refusal, ticket = registry.admit(name, body)
    assert refusal is None
    registry.settle(name, ticket, None)

    assert rs.token_status(registry._token_histories[name], registry._clock())["used"] > 0


def test_reservation_follows_the_composed_max_tokens(monkeypatch):
    # The reservation is what the forwarded request permits: the composed
    # max_tokens, which already carries the reasoning allowance when the
    # composed request reasons, and does not when it does not.
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "m")
    monkeypatch.setenv("STREAM_REASONING_ALLOWANCE", "100")
    body = json.dumps({"model": "m", "messages": []}).encode()

    reasoning = {"model": "m", "max_tokens": 400, "reasoning_effort": "low"}
    composed, _ = rs.compose_body(body, reasoning, rs.reasoning_allowance())
    assert rs.reservation_for(composed, 1000) == rs.estimate_prompt_tokens(composed) + 500

    plain = {"model": "m", "max_tokens": 400}
    quiet = json.dumps({"model": "m", "messages": [], "reasoning_effort": "none"}).encode()
    composed, _ = rs.compose_body(quiet, plain, rs.reasoning_allowance())
    assert rs.reservation_for(composed, 1000) == rs.estimate_prompt_tokens(composed) + 400


def test_a_request_without_any_max_tokens_reserves_the_whole_allowance(monkeypatch):
    # With no cap declared and none in the request, the response may be as
    # large as the upstream permits, so the request holds the hour's allowance
    # until it settles: concurrent uncapped requests contend rather than each
    # seeing an empty window.
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "m")
    registry = rs.StreamRegistry()
    name = _declared(registry, token_budget=1000)
    body = json.dumps({"model": "m", "messages": []}).encode()

    _, refusal, ticket = registry.admit(name, body)
    assert refusal is None
    assert rs.token_status(registry._token_histories[name], registry._clock())["used"] >= 1000
    _, refusal, _ = registry.admit(name, body)
    assert refusal is not None and refusal[0] == 429

    registry.settle(name, ticket, 10)
    _, refusal, _ = registry.admit(name, body)
    assert refusal is None


def test_a_request_carrying_its_own_max_tokens_reserves_that(monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "m")
    registry = rs.StreamRegistry()
    name = _declared(registry, token_budget=1000)
    body = json.dumps({"model": "m", "messages": [], "max_tokens": 300}).encode()

    _, refusal, _ = registry.admit(name, body)
    assert refusal is None
    used = rs.token_status(registry._token_histories[name], registry._clock())["used"]
    assert 300 <= used < 400


def test_settle_accepts_a_float_total(monkeypatch):
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", "m")
    registry = rs.StreamRegistry()
    name = _declared(registry, token_budget=1000, max_tokens=400)
    body = json.dumps({"model": "m", "messages": []}).encode()

    _, _, ticket = registry.admit(name, body)
    registry.settle(name, ticket, 10.0)

    assert rs.token_status(registry._token_histories[name], registry._clock())["used"] == 10


def test_readme_states_what_a_request_in_flight_counts():
    text = rs.README_TEXT
    assert "in flight" in text
    assert "until its usage is known" in text
