import copy

import chassis


def _assistant(content="", tool_calls=None):
    m = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = [
            {
                "id": tc_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
            for tc_id, name in tool_calls
        ]
    return m


def _tool(tc_id, name="read_file", content="ok"):
    return {"role": "tool", "tool_call_id": tc_id, "name": name, "content": content}


def test_clean_history_is_unchanged():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        _assistant("a", tool_calls=[("c1", "read_file")]),
        _tool("c1"),
        _assistant("done"),
    ]
    assert chassis.repair_send_view(messages) == messages


def test_orphaned_tool_results_are_dropped():
    # INC10 regression shape: an archive/trim removed assistant tool_calls
    # messages but left their tool results behind.
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        _tool("gone-1", name="conversation_archive"),
        _tool("gone-2", name="git_op"),
        _assistant("a", tool_calls=[("c1", "read_file")]),
        _tool("c1"),
    ]
    repaired = chassis.repair_send_view(messages)
    ids = [m.get("tool_call_id") for m in repaired if m.get("role") == "tool"]
    assert ids == ["c1"]


def test_tool_result_not_matching_open_calls_is_dropped():
    messages = [
        _assistant("a", tool_calls=[("c1", "read_file")]),
        _tool("c1"),
        _tool("stale", name="write_file"),
    ]
    repaired = chassis.repair_send_view(messages)
    ids = [m.get("tool_call_id") for m in repaired if m.get("role") == "tool"]
    assert ids == ["c1"]


def test_unanswered_tool_call_gets_synthetic_result():
    messages = [
        _assistant("a", tool_calls=[("c1", "read_file"), ("c2", "validate")]),
        _tool("c1"),
        {"role": "user", "content": "next"},
    ]
    repaired = chassis.repair_send_view(messages)
    assert [m["role"] for m in repaired] == ["assistant", "tool", "tool", "user"]
    synthetic = repaired[2]
    assert synthetic["tool_call_id"] == "c2"
    assert synthetic["name"] == "validate"
    assert synthetic["content"] == "result unavailable"


def test_unanswered_tool_call_at_end_gets_synthetic_result():
    messages = [_assistant("a", tool_calls=[("c1", "read_file")])]
    repaired = chassis.repair_send_view(messages)
    assert repaired[-1]["role"] == "tool"
    assert repaired[-1]["tool_call_id"] == "c1"


def test_input_is_not_mutated():
    messages = [
        _tool("orphan"),
        _assistant("a", tool_calls=[("c1", "read_file")]),
    ]
    snapshot = copy.deepcopy(messages)
    chassis.repair_send_view(messages)
    assert messages == snapshot


def test_send_path_applies_repair(monkeypatch):
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            import types

            message = types.SimpleNamespace(content="hi", tool_calls=None, reasoning_content=None)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    import types

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=_Completions()))
    tools = types.SimpleNamespace(schemas=[], tools={})
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        _tool("gone-1"),
    ]
    chassis.run_agent_loop(client, "m", messages, tools, max_turns=1)
    sent_roles = [m["role"] for m in captured["messages"]]
    assert "tool" not in sent_roles
