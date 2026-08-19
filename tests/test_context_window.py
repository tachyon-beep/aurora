import chassis


def test_clip_returns_everything_when_under_budget():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "hi"}]
    assert chassis.clip_to_window(msgs, budget_tokens=10000) == msgs


def test_clip_disabled_when_budget_zero():
    msgs = [{"role": "system", "content": "S"}] + [
        {"role": "user", "content": "x" * 9000} for _ in range(5)
    ]
    assert chassis.clip_to_window(msgs, budget_tokens=0) == msgs


def test_clip_pins_system_and_windows_recent():
    msgs = [{"role": "system", "content": "S"}]
    msgs += [{"role": "user", "content": "x" * 4000} for _ in range(20)]
    out = chassis.clip_to_window(msgs, budget_tokens=2000)
    assert out[0] == msgs[0]
    assert out[-1] == msgs[-1]
    assert len(out) < len(msgs)


def test_clip_keeps_at_least_the_latest_message():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "z" * 100000}]
    out = chassis.clip_to_window(msgs, budget_tokens=1)
    assert out[-1] == msgs[-1]
    assert out[0] == msgs[0]


def test_clip_pins_first_user_message_under_tight_budget():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "seed"}]
    msgs += [{"role": "assistant", "content": f"{i:02d}" + "y" * 4000} for i in range(20)]
    out = chassis.clip_to_window(msgs, budget_tokens=2000)
    assert out[0] == msgs[0]
    assert out[1] == msgs[1]
    assert out[-1] == msgs[-1]
    assert len(out) < len(msgs)
    assert msgs[2] not in out


def test_clip_does_not_duplicate_first_user_message_when_in_tail():
    msgs = [{"role": "system", "content": "S"}]
    msgs += [{"role": "assistant", "content": f"{i:02d}" + "y" * 4000} for i in range(10)]
    msgs += [{"role": "user", "content": "seed"}, {"role": "assistant", "content": "tail"}]
    out = chassis.clip_to_window(msgs, budget_tokens=2000)
    assert len(out) < len(msgs)
    assert msgs[1] not in out
    assert out[0] == msgs[0]
    assert out[-1] == msgs[-1]
    assert sum(1 for m in out if m.get("role") == "user") == 1


def test_clip_never_starts_on_an_orphan_tool_result():
    msgs = [
        {"role": "system", "content": "S"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}], "x": "A" * 8000},
        {"role": "tool", "tool_call_id": "a", "content": "r"},
        {"role": "user", "content": "next"},
    ]
    out = chassis.clip_to_window(msgs, budget_tokens=200)
    non_system = [m for m in out if m.get("role") != "system"]
    assert non_system
    assert non_system[0]["role"] != "tool"


def test_reasoning_effort_unset_returns_none(monkeypatch):
    monkeypatch.delenv("REASONING_EFFORT", raising=False)
    monkeypatch.setattr(chassis, "REASONING_EFFORT", "")
    assert chassis.reasoning_effort() is None


def test_reasoning_effort_accepts_listed_levels(monkeypatch):
    for level in chassis.REASONING_EFFORT_LEVELS:
        monkeypatch.setenv("REASONING_EFFORT", level.upper())
        assert chassis.reasoning_effort() == level


def test_reasoning_effort_rejects_unlisted_values(monkeypatch):
    for value in ("maximum", "11", "true", " "):
        monkeypatch.setenv("REASONING_EFFORT", value)
        assert chassis.reasoning_effort() is None


def test_reasoning_effort_module_constant_applies_without_env(monkeypatch):
    monkeypatch.delenv("REASONING_EFFORT", raising=False)
    monkeypatch.setattr(chassis, "REASONING_EFFORT", "high")
    assert chassis.reasoning_effort() == "high"


def _growing_history():
    msgs = [{"role": "system", "content": "S"}]
    msgs += [{"role": "assistant", "content": f"{i:03d}" + "y" * 400} for i in range(60)]
    return msgs


def _window_starts(msgs, budget_tokens, appends=3, **kwargs):
    starts = []
    for i in range(appends + 1):
        out = chassis.clip_to_window(msgs, budget_tokens=budget_tokens, **kwargs)
        starts.append(id(out[1]))
        msgs = msgs + [{"role": "assistant", "content": f"new{i}" + "y" * 400}]
    return starts


def test_clip_evicts_in_chunks_so_the_window_start_is_stable_as_history_grows():
    starts = _window_starts(_growing_history(), 2000, eviction_chunk_tokens=800)
    assert len(set(starts)) <= 2


def test_clip_chunked_eviction_is_on_by_default():
    starts = _window_starts(_growing_history(), 2000)
    assert len(set(starts)) < 4


def test_clip_chunk_of_zero_evicts_per_message():
    starts = _window_starts(_growing_history(), 2000, eviction_chunk_tokens=0)
    assert len(set(starts)) == 4


def test_clip_eviction_chunk_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("CONTEXT_WINDOW_EVICTION_TOKENS", "800")
    starts = _window_starts(_growing_history(), 2000)
    assert len(set(starts)) <= 2


def test_clip_chunked_eviction_still_fits_the_budget_and_keeps_the_latest_message():
    msgs = _growing_history()
    out = chassis.clip_to_window(msgs, budget_tokens=2000, eviction_chunk_tokens=800)
    assert out[-1] is msgs[-1]
    assert chassis._estimate_tokens(out) <= 2000


def test_reasoning_effort_empty_env_falls_back_to_the_constant(monkeypatch):
    monkeypatch.setenv("REASONING_EFFORT", "")
    monkeypatch.setattr(chassis, "REASONING_EFFORT", "low")
    assert chassis.reasoning_effort() == "low"
