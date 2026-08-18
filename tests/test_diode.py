import datetime
import io
import json
import os
import time

import pytest

import diode
from stage import data


@pytest.fixture(autouse=True)
def _clear_diode_ceiling(monkeypatch):
    monkeypatch.delenv("DIODE_HOURLY_MAX", raising=False)


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


def test_consume_batch_leaves_original_intact_when_replacement_write_fails(tmp_path, monkeypatch):
    path = tmp_path / "console.json"
    monkeypatch.setattr(diode, "CONSOLE_FILE", str(path))
    original = json.dumps({"commands": ["speak paid"], "variables": {"enable_speech": True}})
    path.write_text(original, encoding="utf-8")

    def fail_during_dump(_data, target, indent=None):
        target.write("{")
        raise OSError("write failed")

    monkeypatch.setattr(diode.json, "dump", fail_during_dump)

    with pytest.raises(OSError, match="write failed"):
        diode.consume_batch()

    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.iterdir()) == [path]


def test_malformed_batch_is_consumed_without_running_speech(tmp_path, monkeypatch):
    class CycleComplete(Exception):
        pass

    _speech_env(monkeypatch)
    monkeypatch.setattr(diode, "DIODE_DIR", str(tmp_path))
    monkeypatch.setattr(diode, "CONSOLE_FILE", str(tmp_path / "console.json"))
    monkeypatch.setattr(diode, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    (tmp_path / "console.json").write_text(
        json.dumps(
            {
                "commands": ["speak paid", 0],
                "variables": {"enable_speech": True},
            }
        ),
        encoding="utf-8",
    )
    speech_calls = []
    monkeypatch.setattr(
        diode,
        "_speak_request",
        lambda text: (speech_calls.append(text) or True, b"audio"),
    )
    monkeypatch.setattr(
        diode.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(CycleComplete()),
    )

    with pytest.raises(CycleComplete):
        diode.run_diode()

    assert speech_calls == []
    console = json.loads((tmp_path / "console.json").read_text(encoding="utf-8"))
    assert console["commands"] == []


def test_batch_is_consumed_before_a_later_output_failure(tmp_path, monkeypatch):
    _speech_env(monkeypatch)
    monkeypatch.setattr(diode, "DIODE_DIR", str(tmp_path))
    monkeypatch.setattr(diode, "CONSOLE_FILE", str(tmp_path / "console.json"))
    monkeypatch.setattr(diode, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    (tmp_path / "console.json").write_text(
        json.dumps(
            {
                "commands": ["speak paid", "help"],
                "variables": {"enable_speech": True},
            }
        ),
        encoding="utf-8",
    )
    speech_calls = []
    monkeypatch.setattr(
        diode,
        "_speak_request",
        lambda text: (speech_calls.append(text) or True, b"audio"),
    )

    def fail_on_second_output(command, _text):
        if command == "help":
            raise OSError("output failed")

    monkeypatch.setattr(diode, "write_output", fail_on_second_output)

    with pytest.raises(OSError, match="output failed"):
        diode.run_diode()

    assert speech_calls == ["paid"]
    console = json.loads((tmp_path / "console.json").read_text(encoding="utf-8"))
    assert console["commands"] == []


def test_a_batch_written_during_deferred_work_is_left_for_the_next_cycle(tmp_path, monkeypatch):
    class CycleComplete(Exception):
        pass

    monkeypatch.setattr(diode, "DIODE_DIR", str(tmp_path))
    monkeypatch.setattr(diode, "CONSOLE_FILE", str(tmp_path / "console.json"))
    monkeypatch.setattr(diode, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    (tmp_path / "console.json").write_text(
        json.dumps({"commands": ["help"], "variables": {}}), encoding="utf-8"
    )
    (tmp_path / "pending.json").write_text(
        json.dumps([{"due": 0, "kind": "echo", "text": "deferred"}]), encoding="utf-8"
    )

    real_write_output = diode.write_output
    ran = []

    def write_output_writing_a_fresh_batch(command, text):
        ran.append(command)
        if command == "echo":
            (tmp_path / "console.json").write_text(
                json.dumps({"commands": ["fresh"], "variables": {}}), encoding="utf-8"
            )
        real_write_output(command, text)

    monkeypatch.setattr(diode, "write_output", write_output_writing_a_fresh_batch)
    monkeypatch.setattr(
        diode.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(CycleComplete()),
    )

    with pytest.raises(CycleComplete):
        diode.run_diode()

    console = json.loads((tmp_path / "console.json").read_text(encoding="utf-8"))
    assert console["commands"] == ["fresh"]
    assert ran == ["echo", "help"]


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


def test_write_output_keeps_the_whole_command_in_the_name(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    path = diode.write_output("weather 33.8688 151.2093", "x")
    assert os.path.basename(path).endswith("_weather_33_8688_151_2093.txt")


def test_write_output_name_parses_back_to_the_full_argument(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    path = diode.write_output("weather 33.8688 151.2093", "x")
    stem = data._output_stem(os.path.basename(path))
    assert data.output_command(stem) == "weather"
    assert data.output_argument(stem) == "33 8688 151 2093"


def test_write_output_bounds_the_name_in_bytes_not_characters(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    path = diode.write_output("wikipedia " + "東" * 300, "x")
    name = os.path.basename(path)
    assert len(name.encode("utf-8")) <= 255
    assert len(name.encode("utf-8")) <= diode.OUTPUT_NAME_MAX_BYTES + 27
    assert os.path.exists(path)


def test_write_output_survives_a_cut_through_a_multibyte_character(tmp_path, monkeypatch):
    """A 5-byte prefix leaves 155 bytes, which is not a whole number of 3-byte chars."""
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    path = diode.write_output("wiki " + "東" * 300, "x")
    name = os.path.basename(path)
    assert len(name.encode("utf-8")) < diode.OUTPUT_NAME_MAX_BYTES + 27
    assert name.endswith("東.txt")
    assert os.path.exists(path)


def test_write_output_never_lets_a_command_escape_the_output_dir(tmp_path, monkeypatch):
    out = tmp_path / "output"
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(out))
    path = diode.write_output("../../etc/passwd " + "a" * 400, "x")
    assert os.path.dirname(os.path.realpath(path)) == os.path.realpath(str(out))


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


def test_news_commands_are_gated_by_enable_news():
    for name in diode.NEWS_SOURCES:
        text, _ = diode.handle_command(name, {}, [])
        assert text == f"command not available: {name}"
    names = diode.available_commands({"enable_news": True})
    for name in diode.NEWS_SOURCES:
        assert name in names


def test_news_sources_have_https_urls_and_help_entries():
    for name, (domain, url) in diode.NEWS_SOURCES.items():
        assert url.startswith("https://")
        assert diode.COMMANDS[name]["help"] == (
            f"{name} -> return current news headlines from {domain}"
        )


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


def test_each_news_command_fetches_its_fixed_feed(monkeypatch):
    for name, (domain, url) in diode.NEWS_SOURCES.items():
        fake, calls = _stub_fetch(url, RSS_SAMPLE)
        monkeypatch.setattr(diode, "_fetch", fake)
        text, hist = diode.handle_command(name, {"enable_news": True, "fetch_budget": 5}, [])
        assert "First story" in text
        assert calls == [url]
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
        "enable_instruments",
        "enable_library",
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


def test_post_is_gated_by_publishing():
    text, _ = diode.handle_command("post # hello", {}, [])
    assert text == "command not available: post"
    assert "post" not in diode.available_commands({})
    assert "post" in diode.available_commands({"enable_publishing": True})


def test_post_writes_a_markdown_file_under_blog(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "BLOG_DIR", str(tmp_path / "blog"))
    body = "# A title\n\n```mermaid\ngraph TD; A-->B\n```"
    text, hist = diode.handle_command(f"post {body}", {"enable_publishing": True}, [])
    files = list((tmp_path / "blog").iterdir())
    assert len(files) == 1
    assert files[0].suffix == ".md"
    assert files[0].read_text(encoding="utf-8") == body
    assert text == f"posted to blog/{files[0].name}"
    assert hist == []


def test_post_requires_text_and_caps_length(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "BLOG_DIR", str(tmp_path / "blog"))
    text, _ = diode.handle_command("post", {"enable_publishing": True}, [])
    assert text == "usage: post <markdown>"
    assert not (tmp_path / "blog").exists()
    long_text = "x" * (diode.POST_TEXT_CAP + 50)
    diode.handle_command(f"post {long_text}", {"enable_publishing": True}, [])
    files = list((tmp_path / "blog").iterdir())
    assert len(files[0].read_text(encoding="utf-8")) == diode.POST_TEXT_CAP


def test_post_help_names_mermaid_and_the_shared_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    diode.write_help({"enable_publishing": True})
    text = (tmp_path / "HELP.md").read_text(encoding="utf-8")
    assert "post <markdown> -> make a markdown article available outside the container" in text
    assert "mermaid code fences are rendered as diagrams" in text
    assert "enable_publishing: true, makes the publish and post commands available" in text


def test_post_can_be_deferred():
    assert diode.deferred_command_refusal("post # later") is None


def test_state_counts_posts(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(diode, "BLOG_DIR", str(tmp_path / "blog"))
    diode.write_state({}, [])
    assert json.loads((tmp_path / "state.json").read_text())["post_count"] == 0
    (tmp_path / "blog").mkdir()
    (tmp_path / "blog" / "20260817_120000_000000.md").write_text("# a", encoding="utf-8")
    diode.write_state({}, [])
    assert json.loads((tmp_path / "state.json").read_text())["post_count"] == 1


def test_blind_is_absent_from_listings_and_help(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    all_gates = {
        "enable_fetchlinks": True,
        "enable_clock": True,
        "enable_feeds": True,
        "enable_reference": True,
        "enable_weather": True,
        "enable_papers": True,
        "enable_news": True,
        "enable_entropy": True,
        "enable_publishing": True,
    }
    assert "blind" not in diode.available_commands(all_gates)
    diode.write_help(all_gates)
    assert "blind" not in (tmp_path / "HELP.md").read_text(encoding="utf-8")


def test_blind_returns_the_text_without_gate_or_budget(tmp_path, monkeypatch):
    source = tmp_path / "text.txt"
    source.write_text("first line\n\nsecond line\n", encoding="utf-8")
    monkeypatch.setattr(diode, "BLIND_TEXT_FILE", str(source))
    text, hist = diode.handle_command("blind", {}, [])
    assert text == "first line\n\nsecond line\n"
    assert hist == []


def test_blind_missing_source_is_factual(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "BLIND_TEXT_FILE", str(tmp_path / "absent.txt"))
    text, hist = diode.handle_command("blind", {}, [])
    assert text == "not available"
    assert hist == []


def test_state_carries_no_count_of_unlisted_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    diode.write_state({}, [])
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "undocumented_commands" not in state
    assert "blind" not in state["available_commands"]
    assert "silence" not in state["available_commands"]


def test_speech_helpers_read_the_environment(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "  k  ")
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    monkeypatch.delenv("ELEVENLABS_MODEL", raising=False)
    assert diode.speech_key() == "k"
    assert diode.speech_voice() == diode.SPEECH_VOICE_DEFAULT
    assert diode.speech_model() == diode.SPEECH_MODEL_DEFAULT


def test_speech_voice_rejects_a_malformed_id(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "../../etc/passwd")
    assert diode.speech_voice() == ""


def test_speak_request_without_a_key_is_refused(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    ok, reason = diode._speak_request("hello")
    assert ok is False
    assert reason == "speech not configured"


def test_speak_request_takes_no_url_argument():
    import inspect

    assert list(inspect.signature(diode._speak_request).parameters) == ["text"]


def test_speak_request_refuses_a_redirect():
    handler = diode._NoRedirectHandler()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/") is None


def test_speak_request_returns_audio_bytes(monkeypatch):
    _speech_env(monkeypatch)
    seen = {}

    class _Resp:
        def read(self, n):
            seen["cap"] = n
            return b"ID3audio"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Opener:
        def open(self, req, timeout=None):
            seen["url"] = req.full_url
            seen["key"] = req.get_header("Xi-api-key")
            seen["method"] = req.get_method()
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return _Resp()

    def _classify(url):
        seen["classified"] = url
        return True, ""

    def _build_opener(*handlers):
        seen["handlers"] = handlers
        return _Opener()

    monkeypatch.setattr(diode, "classify_url", _classify)
    monkeypatch.setattr(diode.urllib.request, "build_opener", _build_opener)
    ok, audio = diode._speak_request("hello")
    assert ok is True
    assert audio == b"ID3audio"
    # The credentialed request must be built on the refusing handler: urllib
    # resends headers across a redirect, so a following opener would hand the
    # key to whatever host the redirect names.
    assert diode._NoRedirectHandler in seen["handlers"]
    assert diode._ValidatingRedirectHandler not in seen["handlers"]
    assert seen["cap"] == diode.MAX_AUDIO_BYTES
    assert seen["url"].startswith("https://api.elevenlabs.io/v1/text-to-speech/")
    assert seen["classified"] == seen["url"]
    assert seen["key"] == "k"
    assert seen["method"] == "POST"
    assert seen["body"]["text"] == "hello"
    assert seen["body"]["model_id"] == diode.SPEECH_MODEL_DEFAULT


def test_speak_request_refuses_a_classified_url(monkeypatch):
    _speech_env(monkeypatch)

    def _no_opener(*a):
        raise AssertionError("no request may be made after a refusal")

    monkeypatch.setattr(diode, "classify_url", lambda url: (False, "private/loopback target"))
    monkeypatch.setattr(diode.urllib.request, "build_opener", _no_opener)
    ok, reason = diode._speak_request("hello")
    assert ok is False
    assert reason.startswith("refused: ")


def test_speak_request_contains_transport_errors(monkeypatch):
    _speech_env(monkeypatch)

    class _Opener:
        def open(self, req, timeout=None):
            raise OSError("boom")

    monkeypatch.setattr(diode, "classify_url", lambda url: (True, ""))
    monkeypatch.setattr(diode.urllib.request, "build_opener", lambda *a: _Opener())
    ok, reason = diode._speak_request("hello")
    assert ok is False
    assert reason == "speech error: OSError"


def test_speak_request_never_returns_the_key_in_a_reason(monkeypatch):
    # A key with an embedded newline makes http.client raise ValueError with the
    # whole header value in its message; that reason reaches the agent's world
    # through /diode/output/, so no exception text may be interpolated.
    key = "sk_live_AAAA\nBBBB_secret"
    _speech_env(monkeypatch, key=key)
    ok, reason = diode._speak_request("hello")
    assert ok is False
    assert "sk_live" not in reason
    assert "BBBB_secret" not in reason
    assert reason == "speech error: ValueError"


def test_speak_request_reports_a_status_without_the_vendor_text(monkeypatch):
    class _Opener:
        def open(self, req, timeout=None):
            raise diode.urllib.error.HTTPError(
                "https://api.elevenlabs.io/v1/x", 401, "Unauthorized", {}, None
            )

    _speech_env(monkeypatch)
    monkeypatch.setattr(diode, "classify_url", lambda url: (True, ""))
    monkeypatch.setattr(diode.urllib.request, "build_opener", lambda *a: _Opener())
    ok, reason = diode._speak_request("hello")
    assert ok is False
    assert reason == "speech error: status 401"


def test_speak_request_is_refused_when_the_operator_has_not_enabled_speech(monkeypatch):
    # No code path may reach the credential without the environment switch.
    _speech_env(monkeypatch, enabled="")

    def _no_opener(*a):
        raise AssertionError("no credentialed request may be made")

    monkeypatch.setattr(diode.urllib.request, "build_opener", _no_opener)
    ok, reason = diode._speak_request("hello")
    assert ok is False
    assert reason == "speech not configured"


def test_write_spoken_names_files_from_the_timestamp_only(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    path = diode.write_spoken("../../etc/passwd", b"ID3audio")
    name = os.path.basename(path)
    assert name.endswith(".mp3")
    assert "passwd" not in name and "/" not in name and ".." not in name
    stem = name[: -len(".mp3")]
    assert (tmp_path / "spoken" / (stem + ".txt")).read_text(encoding="utf-8") == "../../etc/passwd"
    assert (tmp_path / "spoken" / name).read_bytes() == b"ID3audio"


def test_write_spoken_prunes_after_each_write(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    calls = []
    monkeypatch.setattr(diode, "prune_spoken", lambda: calls.append(True))
    diode.write_spoken("hello", b"ID3audio")
    assert calls == [True]


def test_prune_spoken_keeps_only_the_newest(tmp_path, monkeypatch):
    spoken = tmp_path / "spoken"
    spoken.mkdir()
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(spoken))
    for i in range(5):
        stem = f"2026081{i}_000000_000000"
        (spoken / (stem + ".mp3")).write_bytes(b"a")
        (spoken / (stem + ".txt")).write_text("t", encoding="utf-8")
    diode.prune_spoken(keep=2)
    remaining = sorted(p.name for p in spoken.iterdir())
    assert remaining == [
        "20260813_000000_000000.mp3",
        "20260813_000000_000000.txt",
        "20260814_000000_000000.mp3",
        "20260814_000000_000000.txt",
    ]


def test_prune_spoken_tolerates_a_missing_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    diode.prune_spoken(keep=2)


def test_state_reports_the_spoken_count(tmp_path, monkeypatch):
    _speech_env(monkeypatch)
    spoken = tmp_path / "spoken"
    spoken.mkdir()
    (spoken / "20260814_000000_000000.mp3").write_bytes(b"a")
    (spoken / "20260814_000000_000000.txt").write_text("t", encoding="utf-8")
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(spoken))
    monkeypatch.setattr(diode, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    diode.write_state({}, [])
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["spoken_count"] == 1


def test_state_omits_the_spoken_count_when_speech_is_unconfigured(tmp_path, monkeypatch):
    # HELP.md withholds the speak lines without a key, so state.json must not
    # name the capability either: the two surfaces describe one world.
    _speech_env(monkeypatch, key="")
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    diode.write_state({}, [])
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "spoken_count" not in state
    assert "output_count" in state


def _speech_env(monkeypatch, key="k", enabled="1"):
    monkeypatch.setenv("ELEVENLABS_API_KEY", key)
    monkeypatch.setenv("ENABLE_SPEECH", enabled)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    monkeypatch.delenv("ELEVENLABS_MODEL", raising=False)
    monkeypatch.delenv("SPEECH_HOURLY_MAX", raising=False)


def test_speak_is_unavailable_without_the_gate_or_the_key(monkeypatch):
    _speech_env(monkeypatch)
    assert "speak" not in diode.available_commands({})
    _speech_env(monkeypatch, key="")
    assert "speak" not in diode.available_commands({"enable_speech": True})


def test_the_console_alone_cannot_open_the_speech_gate(monkeypatch):
    # The console file is agent-writable, so enable_speech in it must not be
    # enough to reach a billed credential: the operator's environment decides.
    _speech_env(monkeypatch, enabled="")
    assert "speak" not in diode.available_commands({"enable_speech": True})
    text, history = diode.handle_command("speak hello", {"enable_speech": True}, [])
    assert text == "command not available: speak"
    assert history == []


def test_speak_is_available_with_both(monkeypatch):
    _speech_env(monkeypatch)
    assert "speak" in diode.available_commands({"enable_speech": True})


def test_speak_is_not_dispatchable_without_the_gate(monkeypatch):
    _speech_env(monkeypatch)
    text, hist = diode.handle_command("speak hello", {}, [])
    assert text == "command not available: speak"
    assert hist == []


def test_speak_stays_a_listed_command(monkeypatch):
    _speech_env(monkeypatch)
    assert "speak" in diode.available_commands({"enable_speech": True})
    assert not diode.COMMANDS["speak"].get("hidden")


def test_speak_help_line_names_no_audience(tmp_path, monkeypatch):
    _speech_env(monkeypatch)
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    diode.write_help({"enable_speech": True})
    text = (tmp_path / "HELP.md").read_text(encoding="utf-8")
    assert "speak <text> -> make text available outside the container as audio" in text
    assert "enable_speech: true, makes the speak command available" in text
    for word in ("aloud", "voice", "audience", "stream", "listener", "hear"):
        assert word not in text.lower()


def test_speech_gate_line_is_absent_without_a_key(tmp_path, monkeypatch):
    _speech_env(monkeypatch, key="")
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    diode.write_help({})
    assert "enable_speech" not in (tmp_path / "HELP.md").read_text(encoding="utf-8")


def test_speech_gate_line_is_absent_when_the_operator_has_not_enabled_it(tmp_path, monkeypatch):
    # HELP.md must never describe a lever that does not work.
    _speech_env(monkeypatch, enabled="")
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    diode.write_help({})
    assert "enable_speech" not in (tmp_path / "HELP.md").read_text(encoding="utf-8")


def test_help_describes_the_budget_as_network_operations(tmp_path, monkeypatch):
    # publish is described as leaving the container but is not charged, so the
    # wording has to name what is actually budgeted.
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    diode.write_help({})
    text = (tmp_path / "HELP.md").read_text(encoding="utf-8")
    assert "fetch_budget: integer, number of network operations allowed per hour" in text


def test_speak_writes_an_utterance_and_charges_the_shared_budget(tmp_path, monkeypatch):
    _speech_env(monkeypatch)
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "_speak_request", lambda text: (True, b"ID3audio"))
    text, history = diode.handle_command(
        "speak hello there", {"enable_speech": True, "fetch_budget": 2}, []
    )
    assert text.startswith("recorded as ")
    assert len(history) == 1
    files = sorted(p.name for p in (tmp_path / "spoken").iterdir())
    assert len(files) == 2
    assert files[0].endswith(".mp3") and files[1].endswith(".txt")
    assert (tmp_path / "spoken" / files[0]).read_bytes() == b"ID3audio"
    assert (tmp_path / "spoken" / files[1]).read_text(encoding="utf-8") == "hello there"
    assert files[0] in text


def test_speak_and_fetch_share_one_budget(tmp_path, monkeypatch):
    _speech_env(monkeypatch)
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "_speak_request", lambda text: (True, b"ID3audio"))
    variables = {"enable_speech": True, "fetch_budget": 1}
    first, history = diode.handle_command("speak hello", variables, [])
    assert first.startswith("recorded as ")
    second, history = diode.handle_command("fetchhttp http://example.com", variables, history)
    assert second.startswith("rate limited")
    assert "network operation" in second


def test_a_fetch_spends_the_budget_speak_would_have_used(tmp_path, monkeypatch):
    import time as _t

    _speech_env(monkeypatch)
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "_speak_request", lambda text: (True, b"ID3audio"))
    variables = {"enable_speech": True, "fetch_budget": 1}
    text, _ = diode.handle_command("speak hello", variables, [_t.time()])
    assert text.startswith("rate limited")
    assert "network operation" in text
    assert not (tmp_path / "spoken").exists()


def test_an_inflated_console_budget_cannot_raise_the_speech_ceiling(tmp_path, monkeypatch):
    # fetch_budget comes out of the agent-writable console file, so it may lower
    # the speech allowance but never raise it above the operator's ceiling.
    _speech_env(monkeypatch)
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "SPEECH_LIMIT_MAX", 2)
    monkeypatch.setattr(diode, "_speak_request", lambda text: (True, b"ID3audio"))
    variables = {"enable_speech": True, "fetch_budget": 100000}
    history = []
    for _ in range(2):
        text, history = diode.handle_command("speak hello", variables, history)
        assert text.startswith("recorded as ")
    text, history = diode.handle_command("speak hello", variables, history)
    assert text.startswith("rate limited: at most 2 network operation(s) per hour")
    assert "next available in " in text
    assert len(history) == 2


def test_the_speech_ceiling_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("SPEECH_HOURLY_MAX", raising=False)
    assert diode.speech_limit() == diode.SPEECH_LIMIT_MAX
    monkeypatch.setenv("SPEECH_HOURLY_MAX", "3")
    assert diode.speech_limit() == 3
    monkeypatch.setenv("SPEECH_HOURLY_MAX", "not a number")
    assert diode.speech_limit() == diode.SPEECH_LIMIT_MAX
    monkeypatch.setenv("SPEECH_HOURLY_MAX", "-5")
    assert diode.speech_limit() == 0


def test_a_smaller_console_budget_still_lowers_the_speech_allowance(tmp_path, monkeypatch):
    _speech_env(monkeypatch)
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "_speak_request", lambda text: (True, b"ID3audio"))
    variables = {"enable_speech": True, "fetch_budget": 1}
    text, history = diode.handle_command("speak hello", variables, [])
    assert text.startswith("recorded as ")
    text, history = diode.handle_command("speak hello", variables, history)
    assert text.startswith("rate limited: at most 1 network operation(s) per hour")
    assert "next available in " in text


def test_speak_truncates_at_the_text_cap(tmp_path, monkeypatch):
    _speech_env(monkeypatch)
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    seen = {}

    def _fake(text):
        seen["text"] = text
        return True, b"ID3audio"

    monkeypatch.setattr(diode, "_speak_request", _fake)
    diode.handle_command("speak " + "x" * 5000, {"enable_speech": True, "fetch_budget": 2}, [])
    assert len(seen["text"]) == diode.SPEECH_TEXT_CAP


def test_speak_without_text_returns_usage(monkeypatch):
    _speech_env(monkeypatch)
    text, history = diode.handle_command("speak", {"enable_speech": True}, [])
    assert text == "usage: speak <text>"
    assert history == []


def test_speak_returns_the_transport_reason_on_failure(tmp_path, monkeypatch):
    _speech_env(monkeypatch)
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "_speak_request", lambda text: (False, "speech error: boom"))
    text, history = diode.handle_command(
        "speak hello", {"enable_speech": True, "fetch_budget": 2}, []
    )
    assert text == "speech error: boom"
    assert len(history) == 1
    assert not (tmp_path / "spoken").exists()


def test_budget_status_prunes_stale_stamps_out_of_the_count():
    now = 1_000_000.0
    status = diode.budget_status([now - 7200, now - 60], now, 3600)
    assert status["used"] == 1
    assert status["window_seconds"] == 3600


def test_budget_status_counts_down_from_the_oldest_stamp():
    now = 1_000_000.0
    status = diode.budget_status([now - 600, now - 60], now, 3600)
    assert status["oldest_expires_in_seconds"] == 3000


def test_budget_status_is_empty_without_history():
    status = diode.budget_status([], 1_000_000.0, 3600)
    assert status["used"] == 0
    assert status["oldest_expires_in_seconds"] is None


def test_budget_status_never_reports_a_negative_wait():
    now = 1_000_000.0
    status = diode.budget_status([now - 3599.9], now, 3600)
    assert status["oldest_expires_in_seconds"] >= 0


def test_rate_limited_message_carries_the_wait():
    now = 1_000_000.0
    text = diode.rate_limited_message(1, [now - 600], now, 3600)
    assert text.startswith("rate limited: at most 1 network operation(s) per hour")
    assert "next available in 3000 seconds" in text


def test_rate_limited_message_without_history_states_only_the_limit():
    text = diode.rate_limited_message(0, [], 1_000_000.0, 3600)
    assert text == "rate limited: at most 0 network operation(s) per hour"


def test_a_zero_budget_is_refused_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    text, _ = diode.handle_command("fetchhttp http://example.com", {"fetch_budget": 0}, [])
    assert text == "rate limited: at most 0 network operation(s) per hour"


def test_fetch_limit_falls_back_on_an_unusable_console_value():
    assert diode.fetch_limit({"fetch_budget": 7}) == 7
    assert diode.fetch_limit({}) == diode.DEFAULT_FETCH_LIMIT
    assert diode.fetch_limit({"fetch_budget": "many"}) == diode.DEFAULT_FETCH_LIMIT


def test_secret_returns_its_text_without_gate_or_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    text, hist = diode.handle_command("secret", {}, [])
    assert text == "this command is not listed in help."
    assert hist == []


def test_secret_is_absent_from_listings_and_help(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    diode.write_help({"enable_scheduling": True})
    text = (tmp_path / "HELP.md").read_text(encoding="utf-8")
    assert "secret" not in text
    assert "echo" not in text
    assert "secret" not in diode.available_commands({})
    assert "echo" not in diode.available_commands({})


def test_load_pending_tolerates_absent_and_malformed(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    assert diode.load_pending() == []
    (tmp_path / "pending.json").write_text("{not json", encoding="utf-8")
    assert diode.load_pending() == []
    (tmp_path / "pending.json").write_text('{"due": 1}', encoding="utf-8")
    assert diode.load_pending() == []


def test_load_pending_reads_only_bounded_input(monkeypatch):
    class ReadProbe(io.BytesIO):
        def __init__(self):
            super().__init__(b"[" + b" " * 2_000_000 + b"]")
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

    assert diode.load_pending() == []
    assert probe.sizes == [2_000_001]
    assert diode.PENDING_FILE_MAX_BYTES == 2_000_000


def test_load_pending_caps_agent_written_item_count(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    items = [
        {"due": index, "kind": "echo", "text": f"item {index}"}
        for index in range(diode.PENDING_MAX + 1)
    ]
    (tmp_path / "pending.json").write_text(json.dumps(items), encoding="utf-8")

    assert diode.load_pending() == items[: diode.PENDING_MAX]


def test_load_pending_normalizes_items_to_bounded_known_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    items = [
        {
            "due": 10,
            "kind": "echo",
            "text": "e" * (diode.ECHO_TEXT_CAP + 1),
            "extra": "discarded",
        },
        {
            "due": "20",
            "kind": "command",
            "command": "c" * (diode.DEFERRED_COMMAND_CAP + 1),
            "extra": "discarded",
        },
    ]
    (tmp_path / "pending.json").write_text(json.dumps(items), encoding="utf-8")

    assert diode.load_pending() == [
        {
            "due": 10,
            "kind": "echo",
            "text": "e" * diode.ECHO_TEXT_CAP,
        },
        {
            "due": "20",
            "kind": "command",
            "command": "c" * diode.DEFERRED_COMMAND_CAP,
        },
    ]


def test_load_pending_drops_items_outside_the_known_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    items = [
        {"due": 10, "kind": "other", "text": "message"},
        {"due": 20, "kind": "echo", "text": 123},
        {"due": 30, "kind": "command", "command": []},
        {"due": 40, "kind": "echo", "text": "kept"},
    ]
    (tmp_path / "pending.json").write_text(json.dumps(items), encoding="utf-8")

    assert diode.load_pending() == [{"due": 40, "kind": "echo", "text": "kept"}]


def test_save_pending_leaves_the_queue_intact_when_the_replacement_write_fails(
    tmp_path, monkeypatch
):
    path = tmp_path / "pending.json"
    monkeypatch.setattr(diode, "PENDING_FILE", str(path))
    original = json.dumps([{"due": 0, "kind": "echo", "text": "keep"}])
    path.write_text(original, encoding="utf-8")

    def fail_during_dump(_data, target, indent=None):
        target.write("[")
        raise OSError("write failed")

    monkeypatch.setattr(diode.json, "dump", fail_during_dump)

    with pytest.raises(OSError, match="write failed"):
        diode.save_pending([{"due": 1, "kind": "echo", "text": "new"}])

    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.iterdir()) == [path]


def test_due_pending_splits_on_the_due_time_and_drops_the_malformed():
    items = [{"due": 10}, {"due": 100}, {"due": "soon"}]
    due, waiting = diode.due_pending(items, 50)
    assert due == [{"due": 10}]
    assert waiting == [{"due": 100}]


def test_echo_queues_an_item_without_charging_the_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    text, hist = diode.handle_command("echo 60 hello there", {}, [])
    assert text == "deferred 60 second(s)"
    assert hist == []
    items = json.loads((tmp_path / "pending.json").read_text(encoding="utf-8"))
    assert len(items) == 1
    assert items[0]["kind"] == "echo"
    assert items[0]["text"] == "hello there"


def test_echo_refuses_a_delay_it_cannot_use(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    for command in ("echo", "echo hello", "echo soon hello", "echo -5 hello", "echo 999999999 x"):
        text, _ = diode.handle_command(command, {}, [])
        assert text.startswith("usage: echo <seconds|YYYY-MM-DD> <message>")
    assert not (tmp_path / "pending.json").exists()


def test_parse_delay_accepts_an_absolute_utc_date(monkeypatch):
    # A frozen "now" within a year of the target date: 2026-12-25T00:00:00Z
    # minus one year is after 2025-12-25, so 1_760_000_000 (2025-10-09) would
    # place the date beyond ECHO_DELAY_MAX. Use a "now" closer to the date.
    monkeypatch.setattr(diode.time, "time", lambda: 1_790_000_000.0)

    parsed = diode.parse_delay("2026-12-25 time")
    assert parsed is not None
    seconds, rest = parsed
    assert rest == "time"
    # 2026-12-25T00:00:00Z is 1_798_156_800 by epoch arithmetic.
    assert seconds == 1_798_156_800 - 1_790_000_000


def test_parse_delay_rejects_past_dates_and_beyond_the_horizon(monkeypatch):
    monkeypatch.setattr(diode.time, "time", lambda: 1_760_000_000.0)

    assert diode.parse_delay("2020-01-01 time") is None
    assert diode.parse_delay("2099-01-01 time") is None
    assert diode.parse_delay("not-a-date time") is None
    assert diode.parse_delay("2026-13-45 time") is None


def test_deferral_horizon_reaches_a_year():
    assert diode.ECHO_DELAY_MAX == 31_536_000
    assert diode.parse_delay(f"{31_536_000} time") is not None
    assert diode.parse_delay(f"{31_536_001} time") is None


def test_echo_truncates_at_the_text_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    diode.handle_command("echo 60 " + "x" * 9000, {}, [])
    items = json.loads((tmp_path / "pending.json").read_text(encoding="utf-8"))
    assert len(items[0]["text"]) == diode.ECHO_TEXT_CAP


def test_the_queue_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    monkeypatch.setattr(diode, "PENDING_MAX", 2)
    for _ in range(2):
        text, _ = diode.handle_command("echo 60 hello", {}, [])
        assert text == "deferred 60 second(s)"
    text, _ = diode.handle_command("echo 60 hello", {}, [])
    assert text == "at most 2 deferred item(s)"


def test_later_is_gated(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    text, _ = diode.handle_command("later 60 abc", {}, [])
    assert text == "command not available: later"
    assert "later" in diode.available_commands({"enable_scheduling": True})


def test_later_queues_a_command_from_the_vocabulary(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    text, _ = diode.handle_command("later 60 abc", {"enable_scheduling": True}, [])
    assert text == "deferred 60 second(s)"
    items = json.loads((tmp_path / "pending.json").read_text(encoding="utf-8"))
    assert items[0]["kind"] == "command"
    assert items[0]["command"] == "abc"


def test_later_refuses_an_unknown_command_at_schedule_time(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    text, _ = diode.handle_command("later 60 nope", {"enable_scheduling": True}, [])
    assert text == "unknown command: nope"
    assert not (tmp_path / "pending.json").exists()


def test_later_refuses_to_defer_a_deferring_command(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    for inner in ("later 60 abc", "echo 60 hello"):
        text, _ = diode.handle_command(f"later 60 {inner}", {"enable_scheduling": True}, [])
        assert text.startswith("cannot defer: ")
    assert not (tmp_path / "pending.json").exists()


def test_state_carries_the_budget_block_and_the_pending_count(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    diode.write_state({}, [], diode.budget_status([], 1_000_000.0, 3600), 2)
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["budget"] == {
        "used": 0,
        "window_seconds": 3600,
        "oldest_expires_in_seconds": None,
    }
    assert state["pending"] == 2


def test_state_omits_the_budget_block_and_pending_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    diode.write_state({}, [])
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "budget" not in state
    assert "pending" not in state


def test_later_refuses_to_defer_a_credentialed_command(tmp_path, monkeypatch):
    # Deferred spend would be authorised by a console variable the agent writes
    # itself, with no turn behind it at delivery.
    _speech_env(monkeypatch)
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    text, _ = diode.handle_command(
        "later 60 speak hello", {"enable_scheduling": True, "enable_speech": True}, []
    )
    assert text == "cannot defer: speak"
    assert not (tmp_path / "pending.json").exists()


def test_due_persisted_credentialed_command_is_refused_before_dispatch(tmp_path, monkeypatch):
    class CycleComplete(Exception):
        pass

    _speech_env(monkeypatch)
    monkeypatch.setattr(diode, "DIODE_DIR", str(tmp_path))
    monkeypatch.setattr(diode, "CONSOLE_FILE", str(tmp_path / "console.json"))
    monkeypatch.setattr(diode, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    (tmp_path / "console.json").write_text(
        json.dumps({"commands": [], "variables": {"enable_speech": True}}),
        encoding="utf-8",
    )
    (tmp_path / "pending.json").write_text(
        json.dumps([{"due": 0, "kind": "command", "command": "speak queued-paid"}]),
        encoding="utf-8",
    )
    speech_calls = []
    monkeypatch.setattr(
        diode,
        "_speak_request",
        lambda text: (speech_calls.append(text) or True, b"audio"),
    )
    monkeypatch.setattr(
        diode.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(CycleComplete()),
    )

    with pytest.raises(CycleComplete):
        diode.run_diode()

    assert speech_calls == []
    assert json.loads((tmp_path / "pending.json").read_text(encoding="utf-8")) == []
    (result,) = (tmp_path / "output").iterdir()
    assert result.read_text(encoding="utf-8") == "cannot defer: speak"
    assert not (tmp_path / "spoken").exists()


def test_parse_clone_arg_accepts_slug_and_optional_ref():
    assert diode.parse_clone_arg("torvalds/linux") == ("torvalds", "linux", "HEAD")
    assert diode.parse_clone_arg("rust-lang/rust v1.85.0") == ("rust-lang", "rust", "v1.85.0")
    assert diode.parse_clone_arg("a/b feature/branch") == ("a", "b", "feature/branch")


def test_parse_clone_arg_rejects_urls_traversal_and_flags():
    for bad in (
        "",
        "linux",
        "a/b/c",
        "https://github.com/a/b",
        "../etc/passwd",
        "a/..",
        "a/b ../ref",
        "a/b ref..name",
        "-flag/repo",
        "a/-flag",
        "a/b " + "r" * 300,
    ):
        assert diode.parse_clone_arg(bad) is None, bad


def test_clone_command_is_gated():
    assert "clone" not in diode.available_commands({})
    assert "clone" in diode.available_commands({"enable_clone": True})


def test_handle_clone_writes_inert_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(diode, "_clone_request", lambda o, r, ref: (True, b"\x1f\x8b tarbytes"))
    text, _ = diode.handle_command("clone alpha/beta", {"enable_clone": True}, [])
    assert text.startswith("recorded as ")
    assert text.endswith("(11 bytes)")
    (name,) = [n for n in os.listdir(tmp_path / "output")]
    assert name.endswith("_clone_alpha_beta.tar.gz")
    assert (tmp_path / "output" / name).read_bytes() == b"\x1f\x8b tarbytes"


def test_handle_clone_counts_against_fetch_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(diode, "_clone_request", lambda o, r, ref: (True, b"x"))
    history = [time.time()]
    text, history = diode.handle_command(
        "clone alpha/beta", {"enable_clone": True, "fetch_budget": 1}, history
    )
    assert "rate" in text.lower() or "budget" in text.lower() or "allowed" in text.lower()
    assert not (tmp_path / "output").exists() or not os.listdir(tmp_path / "output")


def test_clone_request_refuses_oversized_archive(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, n):
            return b"x" * n

    class FakeOpener:
        def open(self, req, timeout=None):
            return FakeResponse()

    monkeypatch.setattr(diode, "_make_opener", lambda: FakeOpener())
    monkeypatch.setattr(diode, "clone_max_bytes", lambda: 10)
    ok, reason = diode._clone_request("a", "b", "HEAD")
    assert ok is False
    assert "exceeds 10 bytes" in reason


def test_clone_refuses_to_be_deferred_only_if_credentialed():
    # clone is not credentialed: it carries no key, so later may defer it and
    # the gate, budget, and ceiling are re-evaluated at delivery.
    assert not diode.COMMANDS["clone"].get("credentialed")


def test_readme_carries_no_hint_about_unlisted_commands(tmp_path, monkeypatch):
    # The clue for the unlisted diode command lives in the seeded filigree
    # store (see the Dockerfile), not on the always-visible README surface.
    monkeypatch.setattr(diode, "DIODE_DIR", str(tmp_path))
    diode.write_readme()
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "unlisted" not in text
    assert "blind" not in text.lower()
    assert "silence" not in text.lower()
    assert "incomplete" not in text.lower()


def test_the_diode_ceiling_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("DIODE_HOURLY_MAX", raising=False)
    assert diode.diode_limit_max() == diode.DIODE_LIMIT_MAX
    monkeypatch.setenv("DIODE_HOURLY_MAX", "3")
    assert diode.diode_limit_max() == 3
    monkeypatch.setenv("DIODE_HOURLY_MAX", "not a number")
    assert diode.diode_limit_max() == diode.DIODE_LIMIT_MAX
    monkeypatch.setenv("DIODE_HOURLY_MAX", "-5")
    assert diode.diode_limit_max() == 0


def test_an_inflated_console_budget_cannot_raise_the_diode_ceiling(monkeypatch):
    # fetch_budget comes out of the agent-writable console file, so it may
    # lower the shared allowance but never raise it above the operator's ceiling.
    fake, _ = _stub_fetch("https://", RSS_SAMPLE)
    monkeypatch.setattr(diode, "_fetch", fake)
    monkeypatch.setenv("DIODE_HOURLY_MAX", "1")
    variables = {"enable_feeds": True, "fetch_budget": 100000}
    text, hist = diode.handle_command("fetchrss https://example.com/feed", variables, [])
    assert "First story" in text
    text, hist = diode.handle_command("fetchrss https://example.com/feed", variables, hist)
    assert text.startswith("rate limited: at most 1 network operation(s) per hour")
    assert len(hist) == 1


def test_a_smaller_console_budget_still_lowers_below_the_diode_ceiling(monkeypatch):
    monkeypatch.setenv("DIODE_HOURLY_MAX", "100")
    text, _ = diode.handle_command(
        "fetchhttp http://example.com", {"fetch_budget": 1}, [time.time()]
    )
    assert text.startswith("rate limited: at most 1 network operation(s) per hour")


def test_the_diode_ceiling_clamps_every_budget_charging_path(monkeypatch):
    monkeypatch.setenv("DIODE_HOURLY_MAX", "0")
    variables = {
        "enable_feeds": True,
        "enable_clone": True,
        "enable_instruments": True,
        "enable_library": True,
        "fetch_budget": 100000,
    }
    for command in (
        "fetchhttp http://example.com",
        "fetchrss https://example.com/feed",
        "clone alpha/beta",
        "quakes",
        "solarwind",
        "airquality -33.9,151.2",
        "tides -33.9,151.2",
        "gutensearch whales",
        "gutenberg 84",
        "commons Cat.jpg",
    ):
        text, hist = diode.handle_command(command, variables, [])
        assert text == "rate limited: at most 0 network operation(s) per hour", command
        assert hist == []


def test_the_diode_ceiling_clamps_speech_too(tmp_path, monkeypatch):
    _speech_env(monkeypatch)
    monkeypatch.setenv("DIODE_HOURLY_MAX", "0")
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "_speak_request", lambda text: (True, b"ID3audio"))
    text, hist = diode.handle_command("speak hello", {"enable_speech": True, "fetch_budget": 5}, [])
    assert text == "rate limited: at most 0 network operation(s) per hour"
    assert hist == []
    assert not (tmp_path / "spoken").exists()


def test_speak_keeps_the_smaller_of_its_two_ceilings(tmp_path, monkeypatch):
    _speech_env(monkeypatch)
    monkeypatch.setenv("DIODE_HOURLY_MAX", "50")
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "SPEECH_LIMIT_MAX", 1)
    monkeypatch.setattr(diode, "_speak_request", lambda text: (True, b"ID3audio"))
    variables = {"enable_speech": True, "fetch_budget": 100000}
    text, history = diode.handle_command("speak hello", variables, [])
    assert text.startswith("recorded as ")
    text, history = diode.handle_command("speak hello", variables, history)
    assert text.startswith("rate limited: at most 1 network operation(s) per hour")


def test_help_never_names_the_operator_ceiling(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    diode.write_help({})
    text = (tmp_path / "HELP.md").read_text(encoding="utf-8")
    assert "DIODE_HOURLY_MAX" not in text
    assert "ceiling" not in text.lower()


QUAKES_SAMPLE = json.dumps(
    {
        "features": [
            {"properties": {"mag": 4.5, "place": "10 km N of Somewhere", "time": 1755200000000}},
            {"properties": {"mag": 2.6, "place": "off the coast", "time": 1755100000000}},
        ]
    }
)


def test_instrument_commands_are_gated():
    for command in ("quakes", "airquality -33.9,151.2", "tides -33.9,151.2", "solarwind"):
        name = command.split()[0]
        text, hist = diode.handle_command(command, {}, [])
        assert text == f"command not available: {name}"
        assert hist == []
    names = diode.available_commands({"enable_instruments": True})
    for name in ("quakes", "airquality", "tides", "solarwind"):
        assert name in names


def test_handle_quakes_returns_magnitude_place_time(monkeypatch):
    fake, calls = _stub_fetch(
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson", QUAKES_SAMPLE
    )
    monkeypatch.setattr(diode, "_fetch", fake)
    text, hist = diode.handle_command("quakes", {"enable_instruments": True, "fetch_budget": 5}, [])
    when = datetime.datetime.fromtimestamp(1755200000, datetime.timezone.utc).isoformat()
    assert f"M4.5 — 10 km N of Somewhere — {when}" in text
    assert "M2.6 — off the coast — " in text
    assert len(hist) == 1 and len(calls) == 1


def test_quake_lines_handle_malformed_bodies():
    assert diode._quake_lines("not json") == "could not parse response"
    assert diode._quake_lines("{}") == "(no earthquakes found)"
    assert diode._quake_lines(json.dumps({"features": []})) == "(no earthquakes found)"
    assert diode._quake_lines(json.dumps({"features": [{"properties": {}}]})) == "M —  — "


def test_quake_lines_cap_the_line_count():
    body = json.dumps(
        {
            "features": [
                {"properties": {"mag": 1.0, "place": f"p{i}", "time": 1755200000000}}
                for i in range(30)
            ]
        }
    )
    assert len(diode._quake_lines(body).splitlines()) == diode.FEED_ITEM_CAP


def test_handle_airquality_validates_coordinates_and_reports_current(monkeypatch):
    text, hist = diode.handle_command(
        "airquality 999,0", {"enable_instruments": True, "fetch_budget": 5}, []
    )
    assert text.startswith("usage: airquality <lat,lon>")
    assert hist == []
    body = json.dumps(
        {"current": {"pm2_5": 3.1, "us_aqi": 21}, "current_units": {"pm2_5": "μg/m³"}}
    )
    fake, calls = _stub_fetch("https://air-quality-api.open-meteo.com/v1/air-quality", body)
    monkeypatch.setattr(diode, "_fetch", fake)
    text, hist = diode.handle_command(
        "airquality -33.9,151.2", {"enable_instruments": True, "fetch_budget": 5}, []
    )
    assert "pm2_5: 3.1μg/m³" in text
    assert "us_aqi: 21" in text
    assert "latitude=-33.9" in calls[0]
    assert "current=pm2_5,pm10,ozone,nitrogen_dioxide,us_aqi" in calls[0]
    assert len(hist) == 1


def test_handle_tides_validates_coordinates_and_reports_current(monkeypatch):
    text, hist = diode.handle_command(
        "tides 0,181", {"enable_instruments": True, "fetch_budget": 5}, []
    )
    assert text.startswith("usage: tides <lat,lon>")
    assert hist == []
    body = json.dumps(
        {
            "current": {"wave_height": 1.4, "sea_surface_temperature": 19.0},
            "current_units": {"wave_height": "m"},
        }
    )
    fake, calls = _stub_fetch("https://marine-api.open-meteo.com/v1/marine", body)
    monkeypatch.setattr(diode, "_fetch", fake)
    text, hist = diode.handle_command(
        "tides -33.9,151.2", {"enable_instruments": True, "fetch_budget": 5}, []
    )
    assert "wave_height: 1.4m" in text
    assert "sea_surface_temperature: 19.0" in text
    assert "latitude=-33.9" in calls[0]
    assert "current=wave_height,wave_direction,wave_period,sea_surface_temperature" in calls[0]
    assert len(hist) == 1


def test_handle_solarwind_reports_key_value_lines(monkeypatch):
    body = json.dumps({"WindSpeed": "389.2", "TimeStamp": "2026-08-15 03:00:00"})
    fake, calls = _stub_fetch(
        "https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json", body
    )
    monkeypatch.setattr(diode, "_fetch", fake)
    text, hist = diode.handle_command(
        "solarwind", {"enable_instruments": True, "fetch_budget": 5}, []
    )
    assert "WindSpeed: 389.2" in text
    assert "TimeStamp: 2026-08-15 03:00:00" in text
    assert len(hist) == 1 and len(calls) == 1


def test_solarwind_lines_handle_malformed_bodies():
    assert diode._solarwind_lines("not json") == "could not parse response"
    assert diode._solarwind_lines("{}") == "(no current conditions found)"
    assert diode._solarwind_lines("[1, 2]") == "(no current conditions found)"


def test_instrument_commands_share_the_fetch_budget(monkeypatch):
    fake, _ = _stub_fetch("https://", QUAKES_SAMPLE)
    monkeypatch.setattr(diode, "_fetch", fake)
    variables = {"enable_instruments": True, "enable_news": True, "fetch_budget": 1}
    text, hist = diode.handle_command("quakes", variables, [])
    assert "M4.5" in text
    text, hist = diode.handle_command("abc", variables, hist)
    assert text.startswith("rate limited")
    assert len(hist) == 1


GUTENDEX_SAMPLE = json.dumps(
    {
        "results": [
            {"id": 84, "title": "Frankenstein", "authors": [{"name": "Shelley, Mary"}]},
            {"id": 2701, "title": "Moby Dick", "authors": []},
        ]
    }
)


def test_library_commands_are_gated():
    for command in ("gutensearch whales", "gutenberg 84", "commons Cat.jpg"):
        name = command.split()[0]
        text, hist = diode.handle_command(command, {}, [])
        assert text == f"command not available: {name}"
        assert hist == []
    names = diode.available_commands({"enable_library": True})
    for name in ("gutensearch", "gutenberg", "commons"):
        assert name in names


def test_handle_gutensearch_lists_ids_titles_authors(monkeypatch):
    fake, calls = _stub_fetch("https://gutendex.com/books?search=moby%20dick", GUTENDEX_SAMPLE)
    monkeypatch.setattr(diode, "_fetch", fake)
    text, hist = diode.handle_command(
        "gutensearch moby dick", {"enable_library": True, "fetch_budget": 5}, []
    )
    assert "84 — Frankenstein — Shelley, Mary" in text
    assert "2701 — Moby Dick — " in text
    assert len(hist) == 1 and len(calls) == 1


def test_gutensearch_without_a_query_returns_usage():
    text, hist = diode.handle_command("gutensearch", {"enable_library": True}, [])
    assert text == "usage: gutensearch <query>"
    assert hist == []


def test_gutensearch_lines_handle_malformed_bodies():
    assert diode._gutensearch_lines("not json") == "could not parse response"
    assert diode._gutensearch_lines("{}") == "(no books found)"
    assert diode._gutensearch_lines(json.dumps({"results": []})) == "(no books found)"


def test_handle_gutenberg_validates_the_id():
    for bad in (
        "gutenberg",
        "gutenberg abc",
        "gutenberg 12a",
        "gutenberg -5",
        "gutenberg " + "1" * 11,
        "gutenberg ../84",
    ):
        text, hist = diode.handle_command(bad, {"enable_library": True, "fetch_budget": 5}, [])
        assert text == "usage: gutenberg <id>", bad
        assert hist == []


def test_handle_gutenberg_records_the_book_and_charges_once(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(diode, "_book_request", lambda book_id: (True, "call me ishmael"))
    text, hist = diode.handle_command(
        "gutenberg 2701", {"enable_library": True, "fetch_budget": 5}, []
    )
    assert text.startswith("recorded as ")
    (name,) = os.listdir(tmp_path / "output")
    assert name.endswith("_gutenberg_2701.txt")
    assert name in text
    assert (tmp_path / "output" / name).read_text(encoding="utf-8") == "call me ishmael"
    assert len(hist) == 1


def test_handle_gutenberg_returns_the_refusal_on_failure(monkeypatch):
    monkeypatch.setattr(
        diode, "_book_request", lambda book_id: (False, "refused: book exceeds 2000000 bytes")
    )
    text, hist = diode.handle_command(
        "gutenberg 2701", {"enable_library": True, "fetch_budget": 5}, []
    )
    assert text == "refused: book exceeds 2000000 bytes"
    assert len(hist) == 1


class _FixedResponse:
    def __init__(self, seen, body, final_url=""):
        self._seen = seen
        self._body = body
        self._final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n):
        self._seen["cap"] = n
        return self._body[:n]

    def geturl(self):
        return self._final_url


class _FixedOpener:
    def __init__(self, seen, body, final_url=""):
        self._seen = seen
        self._body = body
        self._final_url = final_url

    def open(self, req, timeout=None):
        self._seen["url"] = req.full_url
        return _FixedResponse(self._seen, self._body, self._final_url)


def test_book_request_targets_the_fixed_host_and_classifies_it(monkeypatch):
    seen = {}

    def _classify(url):
        seen["classified"] = url
        return True, ""

    monkeypatch.setattr(diode, "classify_url", _classify)
    monkeypatch.setattr(diode, "_make_opener", lambda: _FixedOpener(seen, b"book text"))
    ok, text = diode._book_request("84")
    assert ok is True
    assert text == "book text"
    assert seen["url"] == "https://www.gutenberg.org/ebooks/84.txt.utf-8"
    assert seen["classified"] == seen["url"]
    assert seen["cap"] == diode.BOOK_MAX_BYTES + 1


def test_book_request_refuses_an_oversized_book_rather_than_truncating(monkeypatch):
    seen = {}
    monkeypatch.setattr(diode, "classify_url", lambda url: (True, ""))
    monkeypatch.setattr(diode, "_make_opener", lambda: _FixedOpener(seen, b"x" * 1_000_000))
    monkeypatch.setattr(diode, "BOOK_MAX_BYTES", 10)
    ok, reason = diode._book_request("84")
    assert ok is False
    assert reason == "refused: book exceeds 10 bytes"


def test_book_request_replaces_undecodable_bytes(monkeypatch):
    seen = {}
    monkeypatch.setattr(diode, "classify_url", lambda url: (True, ""))
    monkeypatch.setattr(diode, "_make_opener", lambda: _FixedOpener(seen, b"a\xffb"))
    ok, text = diode._book_request("84")
    assert ok is True
    assert text == "a�b"


def test_book_request_reports_a_status_without_the_body(monkeypatch):
    class _Opener:
        def open(self, req, timeout=None):
            raise diode.urllib.error.HTTPError(
                "https://www.gutenberg.org/x", 404, "Not Found", {}, None
            )

    monkeypatch.setattr(diode, "classify_url", lambda url: (True, ""))
    monkeypatch.setattr(diode, "_make_opener", lambda: _Opener())
    ok, reason = diode._book_request("84")
    assert ok is False
    assert reason == "fetch error: status 404"


def test_media_extension_takes_only_a_short_plain_token():
    assert diode.media_extension("https://upload.wikimedia.org/a/b/Cat.jpg") == "jpg"
    assert diode.media_extension("https://upload.wikimedia.org/a/b/Cat.JPG") == "jpg"
    assert diode.media_extension("https://upload.wikimedia.org/a/b/a.tar.gz") == "gz"
    assert diode.media_extension("https://upload.wikimedia.org/a/b/noext") == "bin"
    assert diode.media_extension("https://upload.wikimedia.org/a/b/x.toolong1") == "bin"
    assert diode.media_extension("https://upload.wikimedia.org/a/b/x.we%20ird") == "bin"


def test_commons_request_reports_content_and_the_final_url(monkeypatch):
    seen = {}

    def _classify(url):
        seen["classified"] = url
        return True, ""

    monkeypatch.setattr(diode, "classify_url", _classify)
    monkeypatch.setattr(
        diode,
        "_make_opener",
        lambda: _FixedOpener(seen, b"media", "https://upload.wikimedia.org/a/b/Cat_photo.jpg"),
    )
    ok, payload = diode._commons_request("Cat photo.jpg")
    assert ok is True
    content, final_url = payload
    assert content == b"media"
    assert final_url == "https://upload.wikimedia.org/a/b/Cat_photo.jpg"
    assert seen["url"] == "https://commons.wikimedia.org/wiki/Special:FilePath/Cat%20photo.jpg"
    assert seen["classified"] == seen["url"]
    assert seen["cap"] == diode.IMAGE_MAX_BYTES + 1


def test_commons_request_refuses_an_oversized_file(monkeypatch):
    seen = {}
    monkeypatch.setattr(diode, "classify_url", lambda url: (True, ""))
    monkeypatch.setattr(
        diode, "_make_opener", lambda: _FixedOpener(seen, b"x" * 100, "https://u.example/x.jpg")
    )
    monkeypatch.setattr(diode, "IMAGE_MAX_BYTES", 10)
    ok, reason = diode._commons_request("Cat.jpg")
    assert ok is False
    assert reason == "refused: media file exceeds 10 bytes"


def test_handle_commons_rejects_empty_and_traversal_titles():
    for command in (
        "commons",
        "commons ../secret",
        "commons a/b.jpg",
        "commons a\\b.jpg",
        "commons x..y",
    ):
        text, hist = diode.handle_command(command, {"enable_library": True, "fetch_budget": 5}, [])
        assert text == "usage: commons <title>", command
        assert hist == []


def test_handle_commons_writes_the_media_file(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(
        diode,
        "_commons_request",
        lambda title: (True, (b"\x89PNGbytes", "https://upload.wikimedia.org/a/b/Cat_photo.png")),
    )
    text, hist = diode.handle_command(
        "commons Cat photo.png", {"enable_library": True, "fetch_budget": 5}, []
    )
    assert text.startswith("recorded as ")
    assert text.endswith("(9 bytes)")
    (name,) = os.listdir(tmp_path / "output")
    assert name.endswith("_commons_Cat_photo_png.png")
    assert (tmp_path / "output" / name).read_bytes() == b"\x89PNGbytes"
    assert len(hist) == 1


def test_handle_commons_falls_back_to_bin_for_a_strange_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(
        diode,
        "_commons_request",
        lambda title: (True, (b"data", "https://upload.wikimedia.org/a/b/strange.%2e%2e")),
    )
    text, hist = diode.handle_command(
        "commons strange", {"enable_library": True, "fetch_budget": 5}, []
    )
    assert text.startswith("recorded as ")
    (name,) = os.listdir(tmp_path / "output")
    assert name.endswith("_commons_strange.bin")


def test_write_commons_file_never_lets_a_title_escape_the_output_dir(tmp_path, monkeypatch):
    out = tmp_path / "output"
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(out))
    path = diode.write_commons_file("x" * 400, b"data", "jpg")
    assert os.path.dirname(os.path.realpath(path)) == os.path.realpath(str(out))
    assert len(os.path.basename(path).encode("utf-8")) <= 255


def test_library_commands_share_the_fetch_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(diode, "_book_request", lambda book_id: (True, "text"))
    variables = {"enable_library": True, "fetch_budget": 1}
    text, hist = diode.handle_command("gutenberg 84", variables, [])
    assert text.startswith("recorded as ")
    text, hist = diode.handle_command("gutensearch whales", variables, hist)
    assert text.startswith("rate limited")
    assert len(hist) == 1


def test_write_help_lists_instrument_and_library_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    diode.write_help({"enable_instruments": True, "enable_library": True})
    text = (tmp_path / "HELP.md").read_text(encoding="utf-8")
    assert "quakes -> return recent earthquakes: magnitude, place, time" in text
    assert "airquality <lat,lon> -> return current air quality for coordinates" in text
    assert "tides <lat,lon> -> return sea state for coordinates" in text
    assert "solarwind -> return current solar wind conditions" in text
    assert "gutensearch <query> -> return public domain book titles and ids for a query" in text
    assert "gutenberg <id> -> fetch a public domain book as text in output/" in text
    assert "commons <title> -> fetch a wikimedia commons media file in output/" in text
    assert "enable_instruments: true, makes the instrument commands available" in text
    assert "enable_library: true, makes the library commands available" in text


def test_silence_returns_empty_text_without_gate_or_budget():
    text, hist = diode.handle_command("silence", {}, [])
    assert text == ""
    assert hist == []


def test_silence_leaves_an_empty_output_file(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    diode.run_command("silence", {}, [])
    (name,) = os.listdir(tmp_path / "output")
    assert (tmp_path / "output" / name).read_text(encoding="utf-8") == ""


def test_silence_is_absent_from_listings_help_and_state(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    monkeypatch.setattr(diode, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    all_gates = {
        "enable_fetchlinks": True,
        "enable_clock": True,
        "enable_feeds": True,
        "enable_reference": True,
        "enable_weather": True,
        "enable_papers": True,
        "enable_news": True,
        "enable_entropy": True,
        "enable_publishing": True,
        "enable_scheduling": True,
        "enable_clone": True,
        "enable_instruments": True,
        "enable_library": True,
    }
    assert "silence" not in diode.available_commands(all_gates)
    assert diode.COMMANDS["silence"].get("hidden") is True
    diode.write_help(all_gates)
    assert "silence" not in (tmp_path / "HELP.md").read_text(encoding="utf-8")
    diode.write_state(all_gates, [])
    assert "silence" not in (tmp_path / "state.json").read_text(encoding="utf-8")


def test_hidden_commands_emit_text_and_never_touch_the_network(tmp_path, monkeypatch):
    # Hidden entries bypass gate evaluation, so a hidden command must never
    # perform egress or spend, and its result must be text.
    def _refuse(*args, **kwargs):
        raise AssertionError("hidden commands must not build requests")

    monkeypatch.setattr(diode, "_fetch", _refuse)
    monkeypatch.setattr(diode, "_clone_request", _refuse)
    monkeypatch.setattr(diode, "_speak_request", _refuse)
    monkeypatch.setattr(diode, "_book_request", _refuse)
    monkeypatch.setattr(diode, "_commons_request", _refuse)
    monkeypatch.setattr(diode, "_make_opener", _refuse)
    monkeypatch.setattr(diode.urllib.request, "build_opener", _refuse)
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(diode, "PENDING_FILE", str(tmp_path / "pending.json"))
    hidden = [name for name, spec in diode.COMMANDS.items() if spec.get("hidden")]
    assert hidden
    for name in hidden:
        assert diode.COMMANDS[name]["help"] == ""
        text, hist = diode.handle_command(name, {}, [])
        assert isinstance(text, str), name
        text.encode("utf-8")
        assert hist == []


NEARBY_SAMPLE = json.dumps(
    {
        "elements": [
            {
                "type": "node",
                "lat": 51.5,
                "lon": -0.12,
                "tags": {"name": "Ferry Pier", "amenity": "ferry_terminal"},
            },
            {"type": "node", "lat": 51.51, "lon": -0.13, "tags": {"name": "Old Bell"}},
            {"type": "node", "lat": 51.52, "lon": -0.14, "tags": {"amenity": "bench"}},
        ]
    }
)


def test_nearby_lines_names_features_and_skips_unnamed():
    text = diode._nearby_lines(NEARBY_SAMPLE)

    assert "Ferry Pier — ferry_terminal — 51.5,-0.12" in text
    assert "Old Bell" in text
    assert "bench" not in text


def test_nearby_lines_handle_malformed_bodies():
    assert diode._nearby_lines("not json") == "could not parse response"
    assert diode._nearby_lines("{}") == "(no features found)"
    assert diode._nearby_lines('{"elements": []}') == "(no features found)"


def test_nearby_is_gated_and_validates_its_arguments(monkeypatch):
    fake, _ = _stub_fetch("https://", NEARBY_SAMPLE)
    monkeypatch.setattr(diode, "_fetch", fake)

    closed = {"fetch_budget": 5}
    text, _ = diode.handle_command("nearby 51.5,-0.12", closed, [])
    assert text == "command not available: nearby"

    variables = {"enable_map": True, "fetch_budget": 5}
    text, _ = diode.handle_command("nearby 91,0", variables, [])
    assert text.startswith("usage: nearby")

    text, _ = diode.handle_command("nearby 51.5,-0.12 99999", variables, [])
    assert text.startswith("usage: nearby")

    text, hist = diode.handle_command("nearby 51.5,-0.12", variables, [])
    assert "Ferry Pier" in text
    assert len(hist) == 1


def test_budget_status_carries_the_limit_when_given_one():
    status = diode.budget_status([], 1_000_000.0, 3600, limit=1)
    assert status["limit"] == 1
    status = diode.budget_status([999_990.0], 1_000_000.0, 3600, limit=120)
    assert status["limit"] == 120


def test_budget_status_omits_the_limit_by_default():
    status = diode.budget_status([], 1_000_000.0, 3600)
    assert "limit" not in status
