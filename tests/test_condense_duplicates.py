import copy

import chassis


def _tool(name, content, call_id="c"):
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


def test_last_duplicate_keeps_full_content_and_earlier_is_stubbed():
    long_content = "x" * 300
    msgs = [
        _tool("read_file", long_content, "a"),
        {"role": "assistant", "content": "between"},
        _tool("read_file", long_content, "b"),
    ]
    out = chassis.condense_duplicate_tool_results(msgs)
    assert out[0]["content"] == "duplicate of a more recent read_file result"
    assert out[2]["content"] == long_content


def test_short_duplicates_are_left_unchanged():
    short_content = "x" * 199
    msgs = [
        _tool("read_file", short_content, "a"),
        _tool("read_file", short_content, "b"),
    ]
    out = chassis.condense_duplicate_tool_results(msgs)
    assert out[0]["content"] == short_content
    assert out[1]["content"] == short_content


def test_non_tool_messages_pass_through_untouched():
    long_content = "y" * 300
    msgs = [
        {"role": "user", "content": long_content},
        {"role": "user", "content": long_content},
        {"role": "assistant", "content": long_content},
    ]
    out = chassis.condense_duplicate_tool_results(msgs)
    assert out == msgs


def test_input_list_and_dicts_are_not_mutated():
    long_content = "z" * 300
    msgs = [
        _tool("read_file", long_content, "a"),
        _tool("read_file", long_content, "b"),
    ]
    snapshot = copy.deepcopy(msgs)
    out = chassis.condense_duplicate_tool_results(msgs)
    assert msgs == snapshot
    assert out is not msgs
    assert out[0] is not msgs[0]


def test_same_content_under_different_tool_names_is_not_a_duplicate():
    long_content = "w" * 300
    msgs = [
        _tool("read_file", long_content, "a"),
        _tool("list_dir", long_content, "b"),
    ]
    out = chassis.condense_duplicate_tool_results(msgs)
    assert out[0]["content"] == long_content
    assert out[1]["content"] == long_content
