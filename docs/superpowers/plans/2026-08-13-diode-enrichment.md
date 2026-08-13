# Diode Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Six new gated diode commands — `fetchrss`, `wikipedia`, `weather`, `arxiv`, `abc`, `entropy` — in the existing closed-vocabulary, budgeted, SSRF-checked style.

**Architecture:** One pure feed parser (`parse_feed`) with an entity-expansion guard serves the three feed-shaped commands; two small pure helpers parse coordinates and JSON API responses; `handle_command` gains one gated dispatch block per command, all fetches flowing through the existing `_fetch`/`check_rate_limit`/`classify_url` machinery. `HELP.md` gains one factual line per gate variable.

**Tech Stack:** Python standard library only (`xml.etree.ElementTree`, `urllib.parse.quote`, `os.urandom`). Tests with pytest following `tests/test_diode.py`'s existing direct-call + monkeypatch pattern.

**Spec:** `docs/superpowers/specs/2026-08-13-diode-enrichment-design.md`

## Global Constraints

- Standard library only. `defusedxml` is NOT available — the DOCTYPE/ENTITY guard is the entity-expansion defense.
- All agent-visible text (help lines, README, output messages) is bland and factual: no suggested uses, personas, jokes, or quest framing.
- Every fetching command consumes the existing `fetch_budget` rate limit and passes `classify_url` + redirect revalidation via `_fetch` — fixed-host URLs included, no exemptions.
- `entropy` performs no network access and does not consume budget.
- No exception may escape `handle_command` for malformed arguments — factual usage lines instead. (The run loop's blanket try/except stays the backstop, not the mechanism.)
- Gate variables: `enable_feeds`, `enable_reference`, `enable_weather`, `enable_papers`, `enable_news`, `enable_entropy`, `enable_publishing` — exactly these names.
- `publish` help text and outputs must be true statements: the text is recorded in the diode volume, which is readable from outside the agent's container. Never claim delivery to any specific audience.
- Only `diode.py` and `tests/test_diode.py` change.
- Run tests: `.venv/bin/python -m pytest tests/test_diode.py -v`, then `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
- Lint before committing: `.venv/bin/ruff format . && .venv/bin/ruff check .`
- Commit messages are factual and benign, and end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `diode.py` | modify | `parse_feed`, `_parse_coordinates`, `_wikipedia_extract`, `_weather_lines`, six `COMMANDS` entries, dispatch, help lines |
| `tests/test_diode.py` | modify | parser/helper units, gate/budget/dispatch tests |

---

### Task 1: Pure parsers and helpers

**Files:**
- Modify: `diode.py` (constants near line 21; new functions after `extract_links`, before `_ValidatingRedirectHandler`)
- Test: `tests/test_diode.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `FEED_ITEM_CAP = 20`, `FEED_TITLE_CAP = 300`, `FEED_SUMMARY_CAP = 500`; `parse_feed(text) -> list[dict] | None` (dicts with keys `title`, `link`, `summary`; `None` on DOCTYPE/ENTITY or unparseable XML); `_parse_coordinates(arg) -> tuple[float, float] | None`; `_wikipedia_extract(body) -> str`; `_weather_lines(body) -> str`. Task 2 dispatches onto all four.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_diode.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_diode.py -v`
Expected: new tests FAIL with `AttributeError: module 'diode' has no attribute 'parse_feed'` (and similar); pre-existing tests pass.

- [ ] **Step 3: Implement the parsers and helpers**

In `diode.py`, after the `FETCH_WINDOW = 3600` line (21), add:

```python
FEED_ITEM_CAP = 20
FEED_TITLE_CAP = 300
FEED_SUMMARY_CAP = 500
```

After `extract_links` (ends line 202), before `_ValidatingRedirectHandler`, add:

```python
def parse_feed(text):
    """Parse RSS or Atom text into a list of title, link, summary dicts.

    Returns None for documents that declare DOCTYPE or ENTITY, and for
    XML that does not parse.
    """
    import xml.etree.ElementTree as ET

    head = text[:4096].lower()
    if "<!doctype" in head or "<!entity" in head:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    def local(tag):
        return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

    items = []
    for element in root.iter():
        if local(element.tag) not in ("item", "entry"):
            continue
        title, link, summary = "", "", ""
        for child in element:
            name = local(child.tag)
            if name == "title":
                title = (child.text or "").strip()
            elif name == "link" and not link:
                link = (child.get("href") or child.text or "").strip()
            elif name in ("description", "summary"):
                summary = (child.text or "").strip()
        items.append(
            {
                "title": title[:FEED_TITLE_CAP],
                "link": link,
                "summary": summary[:FEED_SUMMARY_CAP],
            }
        )
        if len(items) >= FEED_ITEM_CAP:
            break
    return items


def _parse_coordinates(arg):
    """Parse a lat,lon argument with bounds checks; None when invalid."""
    parts = arg.split(",")
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


def _wikipedia_extract(body):
    """Return title, extract, and page URL lines from a summary response."""
    try:
        data = json.loads(body)
    except ValueError:
        return "could not parse response"
    if not isinstance(data, dict) or not data.get("extract"):
        return "(no summary found)"
    lines = [f"# {data.get('title', '')}", "", data["extract"]]
    page = ((data.get("content_urls") or {}).get("desktop") or {}).get("page", "")
    if page:
        lines += ["", page]
    return "\n".join(lines)


def _weather_lines(body):
    """Return current-conditions lines from an open-meteo response."""
    try:
        data = json.loads(body)
    except ValueError:
        return "could not parse response"
    current = data.get("current") if isinstance(data, dict) else None
    if not isinstance(current, dict) or not current:
        return "(no current conditions found)"
    units = data.get("current_units") or {}
    lines = []
    for key, value in current.items():
        lines.append(f"{key}: {value}{units.get(key, '')}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_diode.py -v` then the full suite.
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add diode.py tests/test_diode.py
git commit -m "feat: add feed and api response parsers to the diode

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Commands, dispatch, and help

**Files:**
- Modify: `diode.py` (`COMMANDS` dict at 86–100; `handle_command` at 232–266; `write_help` at 139–151; imports line 9)
- Test: `tests/test_diode.py`

**Interfaces:**
- Consumes: Task 1's `parse_feed`, `_parse_coordinates`, `_wikipedia_extract`, `_weather_lines`, and the existing `_fetch`, `check_rate_limit`, `available_commands`, `DEFAULT_FETCH_LIMIT`, `FETCH_WINDOW`.
- Produces: six new `COMMANDS` entries gated by `enable_feeds` / `enable_reference` / `enable_weather` / `enable_papers` / `enable_news` / `enable_entropy`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_diode.py`:

```python
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
    text, _ = diode.handle_command(
        "arxiv agents", {"enable_papers": True, "fetch_budget": 5}, []
    )
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_diode.py -v`
Expected: the new tests FAIL (`unknown command: fetchrss`, missing gates in help, etc.); Task 1's tests and the pre-existing ones pass.

- [ ] **Step 3: Implement commands, dispatch, and help**

In `diode.py` line 9, extend the import:

```python
from urllib.parse import quote, urlparse
```

Extend `COMMANDS` (after the `"time"` entry):

```python
    "fetchrss": {
        "gate": lambda v: bool(v.get("enable_feeds")),
        "help": "fetchrss <url> -> fetch an rss or atom feed, return title and link lines",
    },
    "wikipedia": {
        "gate": lambda v: bool(v.get("enable_reference")),
        "help": "wikipedia <title> -> return the wikipedia summary for a title",
    },
    "weather": {
        "gate": lambda v: bool(v.get("enable_weather")),
        "help": "weather <lat,lon> -> return current conditions for coordinates",
    },
    "arxiv": {
        "gate": lambda v: bool(v.get("enable_papers")),
        "help": "arxiv <query> -> return recent paper titles and summaries for a query",
    },
    "abc": {
        "gate": lambda v: bool(v.get("enable_news")),
        "help": "abc -> return current news headlines from abc.net.au",
    },
    "entropy": {
        "gate": lambda v: bool(v.get("enable_entropy")),
        "help": "entropy <n> -> return n random bytes as hex",
    },
```

In `handle_command`, after the `time` block (line 249) and before the `fetchhttp`/`fetchlinks` block, add:

```python
    if name == "entropy":
        try:
            count = int(arg)
        except (TypeError, ValueError):
            return "usage: entropy <n> with n from 1 to 256", fetch_history
        if not 1 <= count <= 256:
            return "usage: entropy <n> with n from 1 to 256", fetch_history
        return os.urandom(count).hex(), fetch_history

    if name in ("fetchrss", "wikipedia", "weather", "arxiv", "abc"):
        if name == "fetchrss":
            url = arg
        elif name == "wikipedia":
            title = arg[:200]
            if not title:
                return "usage: wikipedia <title>", fetch_history
            url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(title, safe="")
        elif name == "weather":
            coords = _parse_coordinates(arg)
            if coords is None:
                return (
                    "usage: weather <lat,lon> with lat from -90 to 90 and lon from -180 to 180",
                    fetch_history,
                )
            url = (
                "https://api.open-meteo.com/v1/forecast"
                f"?latitude={coords[0]}&longitude={coords[1]}"
                "&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
                "wind_speed_10m,wind_direction_10m,weather_code"
            )
        elif name == "arxiv":
            query = arg[:200]
            if not query:
                return "usage: arxiv <query>", fetch_history
            url = (
                "https://export.arxiv.org/api/query?search_query=all:"
                + quote(query, safe="")
                + "&max_results=5"
            )
        else:
            url = "https://www.abc.net.au/news/feed/51120/rss.xml"
        try:
            limit = int(variables.get("fetch_budget", DEFAULT_FETCH_LIMIT))
        except (TypeError, ValueError):
            limit = DEFAULT_FETCH_LIMIT
        allowed, fetch_history = check_rate_limit(fetch_history, time.time(), limit, FETCH_WINDOW)
        if not allowed:
            return f"rate limited: at most {limit} fetch(es) per hour", fetch_history
        ok, body = _fetch(url)
        if not ok:
            return body, fetch_history
        if name in ("fetchrss", "abc"):
            items = parse_feed(body)
            if items is None:
                return "could not parse feed", fetch_history
            if not items:
                return "(no feed items found)", fetch_history
            return "\n".join(f"{i['title']} — {i['link']}" for i in items), fetch_history
        if name == "arxiv":
            items = parse_feed(body)
            if items is None:
                return "could not parse feed", fetch_history
            if not items:
                return "(no results found)", fetch_history
            lines = []
            for item in items:
                lines.append(f"{item['title']} — {item['link']}")
                if item["summary"]:
                    lines.append(f"  {item['summary']}")
            return "\n".join(lines), fetch_history
        if name == "wikipedia":
            return _wikipedia_extract(body), fetch_history
        return _weather_lines(body), fetch_history
```

In `write_help`, replace the two variable lines (147–148):

```python
    lines.append("  fetch_budget: integer, number of http-fetch calls allowed per hour")
    lines.append("  enable_fetchlinks: true, makes the link-listing command available")
```

with:

```python
    lines.append("  fetch_budget: integer, number of http-fetch calls allowed per hour")
    lines.append("  enable_fetchlinks: true, makes the link-listing command available")
    lines.append("  enable_clock: true, makes the time command available")
    lines.append("  enable_feeds: true, makes the feed-fetching command available")
    lines.append("  enable_reference: true, makes the wikipedia command available")
    lines.append("  enable_weather: true, makes the weather command available")
    lines.append("  enable_papers: true, makes the arxiv command available")
    lines.append("  enable_news: true, makes the news headline command available")
    lines.append("  enable_entropy: true, makes the entropy command available")
```

(`enable_clock` was previously an unlisted gate; listing it completes the discoverable
landscape the master spec asks for.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_diode.py -v` then the full suite.
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add diode.py tests/test_diode.py
git commit -m "feat: add gated feed, reference, weather, paper, news, and entropy commands

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: The publish command

**Files:**
- Modify: `diode.py` (constants near line 21; `COMMANDS`; `handle_command`; `write_help` variables section)
- Test: `tests/test_diode.py`

**Interfaces:**
- Consumes: existing `DIODE_DIR`, `write_output` timestamp style.
- Produces: `PUBLISHED_DIR = os.path.join(DIODE_DIR, "published")`, `PUBLISH_TEXT_CAP = 4000`, `write_published(text) -> str` (returns the path), and the `publish` command gated by `enable_publishing`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_diode.py`:

```python
def test_publish_is_gated():
    text, _ = diode.handle_command("publish hello", {}, [])
    assert text == "command not available: publish"


def test_publish_records_text_and_confirms_factually(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "PUBLISHED_DIR", str(tmp_path / "published"))
    text, hist = diode.handle_command(
        "publish a short note", {"enable_publishing": True}, []
    )
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_diode.py -v`
Expected: new tests FAIL (`unknown command: publish`, missing attribute `PUBLISHED_DIR`).

- [ ] **Step 3: Implement publish**

In `diode.py`, after the `OUTPUT_DIR` line (15), add:

```python
PUBLISHED_DIR = os.path.join(DIODE_DIR, "published")
```

After `FEED_SUMMARY_CAP` (from Task 1), add:

```python
PUBLISH_TEXT_CAP = 4000
```

Extend `COMMANDS` (after the `"entropy"` entry):

```python
    "publish": {
        "gate": lambda v: bool(v.get("enable_publishing")),
        "help": "publish <text> -> make text available outside the container",
    },
```

After `write_output`, add:

```python
def write_published(text):
    """Write text to PUBLISHED_DIR under a timestamped name, return the path."""
    os.makedirs(PUBLISHED_DIR, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(PUBLISHED_DIR, f"{stamp}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path
```

In `handle_command`, after the `entropy` block, add:

```python
    if name == "publish":
        if not arg:
            return "usage: publish <text>", fetch_history
        path = write_published(arg[:PUBLISH_TEXT_CAP])
        return f"recorded as {os.path.basename(path)}", fetch_history
```

In `write_help`, append after the `enable_entropy` line:

```python
    lines.append("  enable_publishing: true, makes the publish command available")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_diode.py -v` then the full suite.
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add diode.py tests/test_diode.py
git commit -m "feat: add a gated publish command writing to the diode volume

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
