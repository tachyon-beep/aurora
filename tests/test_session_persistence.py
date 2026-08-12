import json
import types

import pytest

import chassis


def _fake_response(content=None, tool_calls=None, reasoning=None):
    message = types.SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        reasoning_content=reasoning,
    )
    choice = types.SimpleNamespace(message=message)
    return types.SimpleNamespace(choices=[choice])


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def _fake_client(responses):
    completions = _FakeCompletions(responses)
    chat = types.SimpleNamespace(completions=completions)
    return types.SimpleNamespace(chat=chat)


def _agent_module():
    module = types.SimpleNamespace()
    module.tools = types.SimpleNamespace(schemas=[], tools={})
    module.conversation_history = []
    module.build_initial_conversation = lambda: [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    return module


def test_main_persists_history_when_loop_ends(tmp_path, monkeypatch):
    session_file = tmp_path / "session_context.json"
    monkeypatch.setattr(chassis, "SESSION_FILE", str(session_file))
    monkeypatch.setattr(chassis, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        chassis, "build_client", lambda: (_fake_client([_fake_response(content="ok")]), "m")
    )
    module = _agent_module()

    with pytest.raises(SystemExit) as exc:
        chassis.main(module)

    assert exc.value.code == 0
    assert session_file.exists()
    saved = json.loads(session_file.read_text(encoding="utf-8"))
    assert saved == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "ok"},
    ]


def test_main_resumes_from_persisted_history(tmp_path, monkeypatch):
    session_file = tmp_path / "session_context.json"
    prior = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "earlier"},
    ]
    session_file.write_text(json.dumps(prior), encoding="utf-8")
    monkeypatch.setattr(chassis, "SESSION_FILE", str(session_file))
    monkeypatch.setattr(chassis, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        chassis, "build_client", lambda: (_fake_client([_fake_response(content="later")]), "m")
    )
    module = _agent_module()

    with pytest.raises(SystemExit):
        chassis.main(module)

    saved = json.loads(session_file.read_text(encoding="utf-8"))
    assert saved == prior + [{"role": "assistant", "content": "later"}]


def test_reasoning_only_reply_persists_assistant_with_content_key(tmp_path, monkeypatch):
    session_file = tmp_path / "session_context.json"
    monkeypatch.setattr(chassis, "SESSION_FILE", str(session_file))
    monkeypatch.setattr(chassis, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        chassis,
        "build_client",
        lambda: (_fake_client([_fake_response(content=None, reasoning="thinking")]), "m"),
    )
    module = _agent_module()

    with pytest.raises(SystemExit):
        chassis.main(module)

    saved = json.loads(session_file.read_text(encoding="utf-8"))
    assert saved[-1]["role"] == "assistant"
    assert "content" in saved[-1]
    assert saved[-1]["content"] == "thinking"


def test_tool_loop_resends_assistant_reasoning_content():
    tool_call = types.SimpleNamespace(
        id="call-1",
        function=types.SimpleNamespace(name="echo", arguments='{"value": "ok"}'),
    )
    client = _fake_client(
        [
            _fake_response(
                content="I'll check.",
                tool_calls=[tool_call],
                reasoning="I should call echo.",
            ),
            _fake_response(content="Done."),
        ]
    )
    tools = types.SimpleNamespace(
        schemas=[{"type": "function", "function": {"name": "echo"}}],
        tools={"echo": lambda value: value},
    )
    messages = [{"role": "user", "content": "Echo ok."}]

    chassis.run_agent_loop(client, "deepseek-v4-pro", messages, tools, max_turns=2)

    assistant = client.chat.completions.calls[1]["messages"][1]
    assert assistant["content"] == "I'll check."
    assert assistant["reasoning_content"] == "I should call echo."
    assert assistant["tool_calls"][0]["id"] == "call-1"


def test_loop_sends_condensed_view_not_raw_history(tmp_path, monkeypatch):
    session_file = tmp_path / "session_context.json"
    big = "x" * 300
    prior = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "a",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "a", "name": "read_file", "content": big},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "b",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "b", "name": "read_file", "content": big},
    ]
    session_file.write_text(json.dumps(prior), encoding="utf-8")
    monkeypatch.setattr(chassis, "SESSION_FILE", str(session_file))
    monkeypatch.setattr(chassis, "load_dotenv", lambda: None)
    client = _fake_client([_fake_response(content="end")])
    monkeypatch.setattr(chassis, "build_client", lambda: (client, "m"))
    module = _agent_module()

    with pytest.raises(SystemExit):
        chassis.main(module)

    sent = client.chat.completions.calls[0]["messages"]
    tool_msgs = [m for m in sent if m.get("role") == "tool"]
    assert tool_msgs[0]["content"] == "duplicate of a more recent read_file result"
    assert tool_msgs[1]["content"] == big
    saved = json.loads(session_file.read_text(encoding="utf-8"))
    assert saved[3]["content"] == big
    assert saved[5]["content"] == big


def test_loop_clips_to_env_window(tmp_path, monkeypatch):
    session_file = tmp_path / "session_context.json"
    prior = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    prior += [{"role": "assistant", "content": f"{i:02d}" + "y" * 2000} for i in range(10)]
    session_file.write_text(json.dumps(prior), encoding="utf-8")
    monkeypatch.setattr(chassis, "SESSION_FILE", str(session_file))
    monkeypatch.setattr(chassis, "load_dotenv", lambda: None)
    monkeypatch.setenv("CONTEXT_WINDOW_TOKENS", "1500")
    client = _fake_client([_fake_response(content="end")])
    monkeypatch.setattr(chassis, "build_client", lambda: (client, "m"))
    module = _agent_module()

    with pytest.raises(SystemExit):
        chassis.main(module)

    sent = client.chat.completions.calls[0]["messages"]
    assert sent[0] == prior[0]
    assert sent[1] == prior[1]
    assert prior[2] not in sent
    assert sent[-1] == prior[-1]
