import json
import os

import diode


def fake_resolver_returning(ip):
    def _resolve(host):
        return [ip]
    return _resolve


def test_classify_url_rejects_non_http_scheme():
    ok, reason = diode.classify_url("file:///etc/passwd", resolver=fake_resolver_returning("1.2.3.4"))
    assert ok is False
    assert "scheme" in reason


def test_classify_url_rejects_loopback():
    ok, reason = diode.classify_url("http://localhost/x", resolver=fake_resolver_returning("127.0.0.1"))
    assert ok is False
    assert "private" in reason or "loopback" in reason


def test_classify_url_rejects_link_local_metadata():
    ok, reason = diode.classify_url("http://metadata/x", resolver=fake_resolver_returning("169.254.169.254"))
    assert ok is False


def test_classify_url_rejects_rfc1918():
    for ip in ("10.0.0.5", "192.168.1.1", "172.16.0.9"):
        ok, _ = diode.classify_url("http://internal/x", resolver=fake_resolver_returning(ip))
        assert ok is False


def test_classify_url_allows_public():
    ok, reason = diode.classify_url("https://example.com/page", resolver=fake_resolver_returning("93.184.216.34"))
    assert ok is True
    assert reason == ""


def test_check_rate_limit_allows_until_limit_then_blocks():
    now = 1000.0
    hist = []
    allowed, hist = diode.check_rate_limit(hist, now, limit=2, window=3600)
    assert allowed is True
    allowed, hist = diode.check_rate_limit(hist, now + 1, limit=2, window=3600)
    assert allowed is True
    allowed, hist = diode.check_rate_limit(hist, now + 2, limit=2, window=3600)
    assert allowed is False


def test_check_rate_limit_recovers_after_window():
    now = 1000.0
    allowed, hist = diode.check_rate_limit([], now, limit=1, window=3600)
    assert allowed is True
    allowed, hist = diode.check_rate_limit(hist, now + 5000, limit=1, window=3600)
    assert allowed is True


def test_available_commands_reflects_variables():
    base = diode.available_commands({})
    assert "help" in base and "fetchhttp" in base
    assert "fetchlinks" not in base and "time" not in base
    unlocked = diode.available_commands({"enable_fetchlinks": True, "enable_clock": True})
    assert "fetchlinks" in unlocked and "time" in unlocked


def test_load_console_handles_missing_and_malformed(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "CONSOLE_FILE", str(tmp_path / "console.json"))
    cmds, vars_ = diode.load_console()
    assert cmds == [] and vars_ == {}
    (tmp_path / "console.json").write_text("{not json", encoding="utf-8")
    cmds, vars_ = diode.load_console()
    assert cmds == [] and vars_ == {}


def test_consume_batch_clears_commands_keeps_variables(tmp_path, monkeypatch):
    f = tmp_path / "console.json"
    monkeypatch.setattr(diode, "CONSOLE_FILE", str(f))
    f.write_text(json.dumps({"commands": ["help"], "variables": {"enable_clock": True}}), encoding="utf-8")
    diode.consume_batch()
    after = json.loads(f.read_text(encoding="utf-8"))
    assert after["commands"] == []
    assert after["variables"] == {"enable_clock": True}


def test_write_help_lists_available_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    diode.write_help({"enable_clock": True})
    text = (tmp_path / "HELP.md").read_text(encoding="utf-8")
    assert "fetchhttp <url>" in text
    assert "time ->" in text  # unlocked by enable_clock
    assert "fetchlinks <url>" not in text  # still gated -> not listed as a command


def test_write_state_records_available_and_variables(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "STATE_FILE", str(tmp_path / "state.json"))
    diode.write_state({"fetch_budget": 3}, ["a"])
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["variables"] == {"fetch_budget": 3}
    assert state["recent_fetches"] == ["a"]
    assert "available_commands" in state
    assert "output_count" in state


def test_redirect_handler_refuses_internal_target():
    import urllib.error

    handler = diode._ValidatingRedirectHandler()
    raised = False
    try:
        handler.redirect_request(None, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data/")
    except urllib.error.HTTPError as e:
        raised = True
        assert "refused redirect" in str(e)
    assert raised


def test_handle_time_writes_utc(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    text, _ = diode.handle_command("time", {"enable_clock": True}, [])
    assert "UTC" in text


def test_handle_unknown_command_is_factual(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    text, _ = diode.handle_command("nope", {}, [])
    assert "unknown" in text.lower()


def test_handle_gated_command_when_locked_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    text, _ = diode.handle_command("time", {}, [])
    assert "not available" in text.lower()


def test_handle_fetch_rate_limited_when_budget_exhausted(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    import time as _t
    text, _ = diode.handle_command("fetchhttp http://example.com", {"fetch_budget": 1}, [_t.time()])
    assert "rate limited" in text.lower()


def test_write_output_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    path = diode.write_output("time", "hello")
    assert os.path.exists(path)
    assert "hello" in open(path, encoding="utf-8").read()
