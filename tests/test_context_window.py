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
