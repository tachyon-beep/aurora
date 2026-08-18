"""The compact tool: a gauge over the send window and a voluntary discard.

The tool reads and mutates agent.conversation_history in place, the same list
object chassis.run_agent_loop iterates, so a discard must use slice
assignment and never rebind the module global.
"""

import json

import agent


def _seed():
    return [
        {"role": "system", "content": "s" * 400},
        {"role": "user", "content": "u" * 400},
    ]


def _turn(i, size=4000):
    return [
        {"role": "assistant", "content": f"a{i}" + "x" * size},
        {"role": "user", "content": f"u{i}" + "y" * size},
    ]


def _fill(n_turns, size=4000):
    history = _seed()
    for i in range(n_turns):
        history += _turn(i, size)
    return history


def _tokens(messages):
    return len(json.dumps(messages, ensure_ascii=False)) // 4


def setup_function(_):
    agent.conversation_history[:] = []


def test_compact_reports_usage_without_mutating(monkeypatch):
    monkeypatch.setenv("CONTEXT_WINDOW_TOKENS", "10000")
    agent.conversation_history[:] = _fill(2)
    before = [dict(m) for m in agent.conversation_history]
    out = agent.compact()
    assert agent.conversation_history == before
    used = _tokens(before)
    assert f"{used}" in out
    assert "10000" in out
    assert f"{round(100 * used / 10000)}%" in out


def test_discard_shrinks_to_the_keep_fraction_in_place(monkeypatch):
    monkeypatch.setenv("CONTEXT_WINDOW_TOKENS", "10000")
    agent.conversation_history[:] = _fill(20)
    held = agent.conversation_history
    out = agent.compact(discard=True)
    assert agent.conversation_history is held
    assert _tokens(agent.conversation_history) <= int(10000 * agent.COMPACT_KEEP_FRACTION)
    assert "deleted" in out


def test_discard_keeps_the_seed_and_the_newest_message(monkeypatch):
    monkeypatch.setenv("CONTEXT_WINDOW_TOKENS", "10000")
    history = _fill(20)
    newest = history[-1]
    agent.conversation_history[:] = history
    agent.compact(discard=True)
    kept = agent.conversation_history
    assert kept[0]["role"] == "system"
    assert kept[1]["content"].startswith("u" * 10)
    assert kept[-1] is newest


def test_discard_never_leaves_a_leading_orphan_tool_result(monkeypatch):
    monkeypatch.setenv("CONTEXT_WINDOW_TOKENS", "1000")
    history = _seed()
    history.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}
            ],
        }
    )
    history.append({"role": "tool", "tool_call_id": "c1", "name": "t", "content": "r" * 4000})
    history += _turn(0)
    agent.conversation_history[:] = history
    agent.compact(discard=True)
    roles = [m["role"] for m in agent.conversation_history]
    first_tool = roles.index("tool") if "tool" in roles else None
    if first_tool is not None:
        assert agent.conversation_history[first_tool - 1].get("tool_calls")


def test_discard_with_a_tiny_budget_keeps_at_least_the_newest(monkeypatch):
    monkeypatch.setenv("CONTEXT_WINDOW_TOKENS", "1")
    history = _fill(3)
    newest = history[-1]
    agent.conversation_history[:] = history
    agent.compact(discard=True)
    assert agent.conversation_history[-1] is newest
    assert len(agent.conversation_history) >= 3


def test_without_a_window_the_report_still_carries_the_usage(monkeypatch):
    monkeypatch.setenv("CONTEXT_WINDOW_TOKENS", "0")
    agent.conversation_history[:] = _fill(2)
    out = agent.compact()
    assert f"{_tokens(agent.conversation_history)}" in out
    assert "%" not in out


def test_compact_is_registered_with_a_boolean_discard():
    assert "compact" in agent.tools.tools
    schema = [s for s in agent.tools.schemas if s["function"]["name"] == "compact"][0]
    assert schema["function"]["parameters"]["properties"]["discard"]["type"] == "boolean"
    assert schema["function"]["parameters"]["required"] == []
