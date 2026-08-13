import json
import os

import diode


def fake_resolver_returning(ip):
    def _resolve(host):
        return [ip]

    return _resolve


def test_classify_url_rejects_non_http_scheme():
    ok, reason = diode.classify_url(
        "file:///etc/passwd", resolver=fake_resolver_returning("1.2.3.4")
    )
    assert ok is False
    assert "scheme" in reason


def test_classify_url_rejects_loopback():
    ok, reason = diode.classify_url(
        "http://localhost/x", resolver=fake_resolver_returning("127.0.0.1")
    )
    assert ok is False
    assert "private" in reason or "loopback" in reason


def test_classify_url_rejects_link_local_metadata():
    ok, reason = diode.classify_url(
        "http://metadata/x", resolver=fake_resolver_returning("169.254.169.254")
    )
    assert ok is False


def test_classify_url_rejects_rfc1918():
    for ip in ("10.0.0.5", "192.168.1.1", "172.16.0.9"):
        ok, _ = diode.classify_url("http://internal/x", resolver=fake_resolver_returning(ip))
        assert ok is False


def test_classify_url_allows_public():
    ok, reason = diode.classify_url(
        "https://example.com/page", resolver=fake_resolver_returning("93.184.216.34")
    )
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
    f.write_text(
        json.dumps({"commands": ["help"], "variables": {"enable_clock": True}}), encoding="utf-8"
    )
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
        handler.redirect_request(
            None, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data/"
        )
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


RSS_SAMPLE = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
<item><title>First story</title><link>https://example.com/1</link>
<description>Summary one</description></item>
<item><title>Second story</title><link>https://example.com/2</link></item>
</channel></rss>"""

ATOM_SAMPLE = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Feed</title>
<entry><title>Paper one</title><link href="https://example.com/abs/1"/>
<summary>About things.</summary></entry>
</feed>"""


def test_parse_feed_reads_rss():
    items = diode.parse_feed(RSS_SAMPLE)
    assert [i["title"] for i in items] == ["First story", "Second story"]
    assert items[0]["link"] == "https://example.com/1"
    assert items[0]["summary"] == "Summary one"
    assert items[1]["summary"] == ""


def test_parse_feed_reads_atom():
    items = diode.parse_feed(ATOM_SAMPLE)
    assert items == [
        {"title": "Paper one", "link": "https://example.com/abs/1", "summary": "About things."}
    ]


def test_parse_feed_rejects_doctype_and_entities():
    assert diode.parse_feed('<!DOCTYPE rss [<!ENTITY a "b">]><rss/>') is None
    assert diode.parse_feed("<!doctype html><html></html>") is None


def test_parse_feed_rejects_malformed_xml():
    assert diode.parse_feed("not xml at all <<<") is None


def test_parse_feed_caps_items_and_titles():
    items_xml = "".join(
        f"<item><title>{'t' * 400}</title><link>https://example.com/{i}</link></item>"
        for i in range(30)
    )
    text = f"<rss><channel>{items_xml}</channel></rss>"
    items = diode.parse_feed(text)
    assert len(items) == diode.FEED_ITEM_CAP
    assert len(items[0]["title"]) == diode.FEED_TITLE_CAP


def test_parse_coordinates_bounds():
    assert diode._parse_coordinates("-33.9,151.2") == (-33.9, 151.2)
    assert diode._parse_coordinates("91,0") is None
    assert diode._parse_coordinates("0,181") is None
    assert diode._parse_coordinates("abc,1") is None
    assert diode._parse_coordinates("1") is None


def test_wikipedia_extract_reads_summary():
    body = json.dumps(
        {
            "title": "Example",
            "extract": "Example is a thing.",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Example"}},
        }
    )
    text = diode._wikipedia_extract(body)
    assert "# Example" in text
    assert "Example is a thing." in text
    assert "https://en.wikipedia.org/wiki/Example" in text
    assert diode._wikipedia_extract("{}") == "(no summary found)"
    assert diode._wikipedia_extract("not json") == "could not parse response"


def test_weather_lines_reads_current():
    body = json.dumps(
        {
            "current": {"temperature_2m": 14.2, "wind_speed_10m": 22.0, "weather_code": 3},
            "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
        }
    )
    text = diode._weather_lines(body)
    assert "temperature_2m: 14.2°C" in text
    assert "wind_speed_10m: 22.0km/h" in text
    assert "weather_code: 3" in text
    assert diode._weather_lines("{}") == "(no current conditions found)"
    assert diode._weather_lines("not json") == "could not parse response"


def test_parse_feed_rejects_doctype_with_leading_padding():
    padded = "<!-- " + "x" * 5000 + ' -->\n<!DOCTYPE rss [<!ENTITY a "b">]><rss/>'
    assert diode.parse_feed(padded) is None


def test_parse_feed_prefers_link_alternate_over_self():
    feed = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Feed</title>
<entry><title>Paper</title>
<link rel="self" href="https://example.com/self"/>
<link rel="alternate" href="https://example.com/article"/>
</entry>
</feed>"""
    items = diode.parse_feed(feed)
    assert items[0]["link"] == "https://example.com/article"


def test_parse_feed_fallback_to_self_link_when_no_alternate():
    feed = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Feed</title>
<entry><title>Paper</title>
<link rel="self" href="https://example.com/self"/>
</entry>
</feed>"""
    items = diode.parse_feed(feed)
    assert items[0]["link"] == "https://example.com/self"


def _stub_fetch(expected_fragment, body):
    calls = []

    def fake(url):
        calls.append(url)
        assert expected_fragment in url
        return True, body

    return fake, calls


def test_new_commands_are_gated():
    for name in ("fetchrss", "wikipedia", "weather", "arxiv", "abc", "entropy"):
        text, _ = diode.handle_command(f"{name} x", {}, [])
        assert text == f"command not available: {name}"


def test_gate_variables_open_new_commands():
    variables = {
        "enable_feeds": True,
        "enable_reference": True,
        "enable_weather": True,
        "enable_papers": True,
        "enable_news": True,
        "enable_entropy": True,
    }
    names = diode.available_commands(variables)
    for name in ("fetchrss", "wikipedia", "weather", "arxiv", "abc", "entropy"):
        assert name in names


def test_handle_fetchrss_writes_feed_lines(monkeypatch):
    fake, calls = _stub_fetch("https://example.com/feed", RSS_SAMPLE)
    monkeypatch.setattr(diode, "_fetch", fake)
    text, hist = diode.handle_command(
        "fetchrss https://example.com/feed", {"enable_feeds": True, "fetch_budget": 5}, []
    )
    assert "First story — https://example.com/1" in text
    assert len(hist) == 1 and len(calls) == 1


def test_handle_abc_uses_fixed_feed(monkeypatch):
    fake, calls = _stub_fetch("abc.net.au", RSS_SAMPLE)
    monkeypatch.setattr(diode, "_fetch", fake)
    text, hist = diode.handle_command("abc", {"enable_news": True, "fetch_budget": 5}, [])
    assert "First story" in text
    assert len(hist) == 1


def test_handle_wikipedia_quotes_title(monkeypatch):
    body = json.dumps({"title": "Ada Lovelace", "extract": "Mathematician."})
    fake, calls = _stub_fetch("rest_v1/page/summary/Ada%20Lovelace", body)
    monkeypatch.setattr(diode, "_fetch", fake)
    text, _ = diode.handle_command(
        "wikipedia Ada Lovelace", {"enable_reference": True, "fetch_budget": 5}, []
    )
    assert "Mathematician." in text


def test_handle_weather_validates_coordinates(monkeypatch):
    text, hist = diode.handle_command(
        "weather 999,0", {"enable_weather": True, "fetch_budget": 5}, []
    )
    assert text.startswith("usage: weather")
    assert hist == []
    body = json.dumps({"current": {"temperature_2m": 20}, "current_units": {}})
    fake, calls = _stub_fetch("latitude=-33.9", body)
    monkeypatch.setattr(diode, "_fetch", fake)
    text, hist = diode.handle_command(
        "weather -33.9,151.2", {"enable_weather": True, "fetch_budget": 5}, []
    )
    assert "temperature_2m: 20" in text
    assert len(hist) == 1


def test_handle_arxiv_includes_summaries(monkeypatch):
    fake, calls = _stub_fetch("export.arxiv.org", ATOM_SAMPLE)
    monkeypatch.setattr(diode, "_fetch", fake)
    text, _ = diode.handle_command("arxiv agents", {"enable_papers": True, "fetch_budget": 5}, [])
    assert "Paper one — https://example.com/abs/1" in text
    assert "About things." in text


def test_handle_entropy_bounds_and_no_budget():
    variables = {"enable_entropy": True}
    text, hist = diode.handle_command("entropy 8", variables, [])
    assert len(text) == 16 and hist == []
    int(text, 16)
    for bad in ("entropy 0", "entropy 1000", "entropy x", "entropy"):
        text, hist = diode.handle_command(bad, variables, [])
        assert text.startswith("usage: entropy")
        assert hist == []


def test_new_fetch_commands_share_budget(monkeypatch):
    fake, _ = _stub_fetch("https://", RSS_SAMPLE)
    monkeypatch.setattr(diode, "_fetch", fake)
    variables = {"enable_feeds": True, "enable_news": True, "fetch_budget": 1}
    text, hist = diode.handle_command("fetchrss https://example.com/feed", variables, [])
    assert "First story" in text
    text, hist = diode.handle_command("abc", variables, hist)
    assert text.startswith("rate limited")


def test_unparseable_feed_is_factual(monkeypatch):
    fake, _ = _stub_fetch("https://", "<!DOCTYPE nope>")
    monkeypatch.setattr(diode, "_fetch", fake)
    text, _ = diode.handle_command(
        "fetchrss https://example.com/feed", {"enable_feeds": True, "fetch_budget": 5}, []
    )
    assert text == "could not parse feed"


def test_write_help_lists_all_gate_variables(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    diode.write_help({})
    text = (tmp_path / "HELP.md").read_text(encoding="utf-8")
    for gate in (
        "enable_fetchlinks",
        "enable_clock",
        "enable_feeds",
        "enable_reference",
        "enable_weather",
        "enable_papers",
        "enable_news",
        "enable_entropy",
    ):
        assert gate in text


def test_publish_is_gated():
    text, _ = diode.handle_command("publish hello", {}, [])
    assert text == "command not available: publish"


def test_publish_records_text_and_confirms_factually(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "PUBLISHED_DIR", str(tmp_path / "published"))
    text, hist = diode.handle_command("publish a short note", {"enable_publishing": True}, [])
    assert hist == []
    files = list((tmp_path / "published").iterdir())
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == "a short note"
    assert files[0].name in text


def test_publish_requires_text_and_caps_length(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "PUBLISHED_DIR", str(tmp_path / "published"))
    text, _ = diode.handle_command("publish", {"enable_publishing": True}, [])
    assert text.startswith("usage: publish")
    long_text = "x" * 5000
    text, _ = diode.handle_command(f"publish {long_text}", {"enable_publishing": True}, [])
    files = list((tmp_path / "published").iterdir())
    assert len(files) == 1
    assert len(files[0].read_text(encoding="utf-8")) == diode.PUBLISH_TEXT_CAP


def test_write_help_lists_publishing_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    diode.write_help({})
    text = (tmp_path / "HELP.md").read_text(encoding="utf-8")
    assert "enable_publishing" in text
