import json
import urllib.request

import pytest

from stage import llm

STAGE_KEY = "stage-key-only"


class _Response:
    def __init__(self, body, status=200):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status

    def read(self, *_args):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


@pytest.mark.parametrize(
    "url,permitted",
    [
        ("https://openrouter.ai/api/v1/chat/completions", True),
        ("https://127.0.0.1/v1/chat/completions", True),
        ("http://localhost:9/v1/chat/completions", True),
        ("http://127.0.0.1:9/v1/chat/completions", True),
        ("http://[::1]:9/v1/chat/completions", True),
        ("http://evil.example.com/v1/chat/completions", False),
        ("http://169.254.169.254/latest/meta-data", False),
        ("file:///etc/passwd", False),
        ("ftp://example.com/x", False),
        ("", False),
    ],
)
def test_permitted_url_allows_https_and_loopback_http_only(url, permitted):
    assert llm._permitted_url(url) is permitted


def test_redirect_handler_refuses_every_redirect():
    handler = llm._NoRedirect()
    for code in (301, 302, 303, 307, 308):
        assert (
            handler.redirect_request(None, None, code, "moved", {}, "https://evil.example") is None
        )


def test_send_builds_an_opener_that_refuses_redirects(monkeypatch):
    seen = {}

    def fake_build_opener(*handlers):
        seen["handlers"] = handlers

        class _Opener:
            def open(self, request, timeout=None):
                return _Response("{}")

        return _Opener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
    llm._send(urllib.request.Request("https://example.com"), 1)
    assert llm._NoRedirect in seen["handlers"]


def test_chat_returns_none_without_a_key(monkeypatch):
    monkeypatch.delenv("STAGE_SUMMARY_API_KEY", raising=False)

    def explode(*_args, **_kwargs):
        raise AssertionError("no request may be made without a key")

    monkeypatch.setattr(llm, "_send", explode)
    assert llm.chat("sys", "user", 100, 0.4) is None


def test_chat_sends_the_key_and_returns_the_cleaned_reply(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    captured = {}

    def fake_send(request, timeout=None):
        captured["request"] = request
        body = json.dumps({"choices": [{"message": {"content": "  A line.  "}}]})
        return _Response(body)

    monkeypatch.setattr(llm, "_send", fake_send)
    assert llm.chat("sys", "user", 100, 0.4) == "A line."
    request = captured["request"]
    assert request.full_url == "https://openrouter.ai/api/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer " + STAGE_KEY
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["messages"][0] == {"role": "system", "content": "sys"}
    assert payload["messages"][1] == {"role": "user", "content": "user"}


def test_chat_refuses_a_non_permitted_base_url(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    monkeypatch.setenv("STAGE_SUMMARY_BASE_URL", "http://evil.example.com/v1")

    def explode(*_args, **_kwargs):
        raise AssertionError("the credential must never leave over plaintext")

    monkeypatch.setattr(llm, "_send", explode)
    assert llm.chat("sys", "user", 100, 0.4) is None


@pytest.mark.parametrize(
    "handler",
    [
        lambda request, timeout=None: (_ for _ in ()).throw(TimeoutError("timed out")),
        lambda request, timeout=None: (_ for _ in ()).throw(OSError("dns")),
        lambda request, timeout=None: _Response("{}", status=500),
        lambda request, timeout=None: _Response("not json at all"),
        lambda request, timeout=None: _Response(json.dumps({"choices": []})),
        lambda request, timeout=None: _Response(json.dumps({"choices": [{}]})),
    ],
)
def test_chat_fails_open_on_every_transport_failure(monkeypatch, handler):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    monkeypatch.setattr(llm, "_send", handler)
    assert llm.chat("sys", "user", 100, 0.4) is None


def test_model_name_reads_an_alternate_variable(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_MODEL", "base/model")
    monkeypatch.delenv("STAGE_COMMENTARY_MODEL", raising=False)
    assert llm.model_name() == "base/model"
    assert llm.model_name("STAGE_COMMENTARY_MODEL", llm.model_name()) == "base/model"
    monkeypatch.setenv("STAGE_COMMENTARY_MODEL", "fast/model")
    assert llm.model_name("STAGE_COMMENTARY_MODEL", llm.model_name()) == "fast/model"


def test_clean_flattens_and_cuts_to_a_sentence():
    assert llm.clean("```\n# H\nOne.\n\n  Two.\n```", 200) == "H One. Two."
    assert llm.clean("One sentence. Two sentence. Three.", 20) == "One sentence."


def test_parse_reply_rejects_content_cut_off_by_the_token_limit():
    raw = json.dumps(
        {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": "This answer stops before it is complete"},
                }
            ]
        }
    )

    assert llm._parse_reply(raw, llm.MAX_OUTPUT_CHARS) is None


def test_source_never_names_the_recorder_credentials():
    with open("stage/llm.py", "r", encoding="utf-8") as f:
        source = f.read()
    assert "OPENROUTER_API_KEY" not in source
    assert "LLM_API_KEY" not in source


def test_records_framing_forbids_following_embedded_instructions():
    text = llm.RECORDS_FRAMING.lower()
    assert "never as instructions" in text
    assert "ignore any instruction" in text


def test_requests_turn_reasoning_off_by_default(monkeypatch):
    """A reasoning model counts its thinking inside max_tokens; the stage asks
    for short lines, so by default every request carries reasoning_effort none
    and max_tokens is sent exactly as given."""
    monkeypatch.delenv("STAGE_LLM_REASONING_EFFORT", raising=False)
    body = llm.request_body("sys", "user", 120, 0.7, "m")
    assert body["reasoning_effort"] == "none"
    assert body["max_tokens"] == 120


def test_a_configured_reasoning_level_adds_the_allowance(monkeypatch):
    monkeypatch.setenv("STAGE_LLM_REASONING_EFFORT", "medium")
    monkeypatch.delenv("STAGE_LLM_REASONING_ALLOWANCE", raising=False)
    body = llm.request_body("sys", "user", 120, 0.7, "m")
    assert body["reasoning_effort"] == "medium"
    assert body["max_tokens"] == 120 + llm.DEFAULT_REASONING_ALLOWANCE
    monkeypatch.setenv("STAGE_LLM_REASONING_ALLOWANCE", "500")
    assert llm.request_body("sys", "user", 120, 0.7, "m")["max_tokens"] == 620


def test_reasoning_off_omits_the_field_and_junk_falls_back(monkeypatch):
    monkeypatch.setenv("STAGE_LLM_REASONING_EFFORT", "off")
    assert "reasoning_effort" not in llm.request_body("sys", "user", 120, 0.7, "m")
    monkeypatch.setenv("STAGE_LLM_REASONING_EFFORT", "turbo")
    assert llm.request_body("sys", "user", 120, 0.7, "m")["reasoning_effort"] == "none"


def test_chat_sends_the_reasoning_field(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", STAGE_KEY)
    monkeypatch.delenv("STAGE_LLM_REASONING_EFFORT", raising=False)
    captured = {}

    def fake_send(request, timeout=None):
        captured["request"] = request
        return _Response(json.dumps({"choices": [{"message": {"content": "A line."}}]}))

    monkeypatch.setattr(llm, "_send", fake_send)
    assert llm.chat("sys", "user", 100, 0.4) == "A line."
    payload = json.loads(captured["request"].data.decode("utf-8"))
    assert payload["reasoning_effort"] == "none"
    assert payload["max_tokens"] == 100
