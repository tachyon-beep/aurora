import datetime
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse

DIODE_DIR = os.environ.get("DIODE_DIR", "/diode")
CONSOLE_FILE = os.path.join(DIODE_DIR, "console.json")
STATE_FILE = os.path.join(DIODE_DIR, "state.json")
HELP_FILE = os.path.join(DIODE_DIR, "HELP.md")
OUTPUT_DIR = os.path.join(DIODE_DIR, "output")
PUBLISHED_DIR = os.path.join(DIODE_DIR, "published")
SPOKEN_DIR = os.path.join(DIODE_DIR, "spoken")
BLIND_TEXT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blind_eternities.txt")

POLL_SECONDS = 5
FETCH_TIMEOUT = 15
MAX_RESPONSE_BYTES = 2_000_000
DEFAULT_FETCH_LIMIT = 1
FETCH_WINDOW = 3600

OUTPUT_NAME_MAX_BYTES = 160

FEED_ITEM_CAP = 20
FEED_TITLE_CAP = 300
FEED_SUMMARY_CAP = 500
PUBLISH_TEXT_CAP = 4000

SPEECH_URL_TEMPLATE = (
    "https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128"
)
SPEECH_VOICE_DEFAULT = "JBFqnCBsd6RMkjVDRZzb"
SPEECH_MODEL_DEFAULT = "eleven_multilingual_v2"
SPEECH_TEXT_CAP = 300
MAX_AUDIO_BYTES = 2_000_000
SPOKEN_KEEP = 20
SPEECH_LIMIT_MAX = 20
VOICE_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")


def _default_resolver(host):
    """Resolve a hostname to its IP address strings."""
    infos = socket.getaddrinfo(host, None)
    return [info[4][0] for info in infos]


def classify_url(url, resolver=_default_resolver):
    """Return (ok, reason). ok is False for non-http(s) or non-public targets.

    Resolves the host and rejects loopback, link-local, private, reserved, or
    multicast addresses (defeats SSRF and DNS-rebinding to internal services).
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"unparseable url: {e}"
    if parsed.scheme not in ("http", "https"):
        return False, f"scheme not allowed: {parsed.scheme or '(none)'}"
    host = parsed.hostname
    if not host:
        return False, "no host"
    try:
        addrs = resolver(host)
    except Exception as e:
        return False, f"resolution failed: {e}"
    if not addrs:
        return False, "no addresses"
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False, f"bad address: {addr}"
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_private
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, f"private/loopback/reserved target: {addr}"
    return True, ""


def check_rate_limit(history, now, limit, window):
    """Token-bucket-ish check. Returns (allowed, new_history).

    history is a list of prior timestamps. Drops entries older than window;
    allows if fewer than limit remain, appending now when allowed.
    """
    recent = [t for t in history if now - t < window]
    if len(recent) >= limit:
        return False, recent
    recent.append(now)
    return True, recent


def _gate_always(variables):
    """Gate function that always returns True."""
    return True


def _speech_gate(variables):
    """Gate function for speak: the operator's configuration and the console variable."""
    return speech_configured() and bool(variables.get("enable_speech"))


COMMANDS = {
    "help": {"gate": _gate_always, "help": "help -> write the current command list to HELP.md"},
    "fetchhttp": {
        "gate": _gate_always,
        "help": "fetchhttp <url> -> fetch a page, return main content as markdown to output/",
    },
    "fetchlinks": {
        "gate": lambda v: bool(v.get("enable_fetchlinks")),
        "help": "fetchlinks <url> -> return the links found on a page",
    },
    "time": {
        "gate": lambda v: bool(v.get("enable_clock")),
        "help": "time -> return the current UTC time",
    },
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
    "publish": {
        "gate": lambda v: bool(v.get("enable_publishing")),
        "help": "publish <text> -> make text available outside the container",
    },
    "speak": {
        "gate": _speech_gate,
        "help": "speak <text> -> make text available outside the container as audio",
    },
    "blind": {
        "gate": _gate_always,
        "help": "",
        "hidden": True,
    },
}


def available_commands(variables):
    """Names of listed commands whose gate is open under the given variables."""
    return [
        name
        for name, spec in COMMANDS.items()
        if spec["gate"](variables) and not spec.get("hidden")
    ]


def undocumented_command_count():
    """Number of commands that are not listed."""
    return len([name for name, spec in COMMANDS.items() if spec.get("hidden")])


def load_console():
    """Read (commands, variables) from CONSOLE_FILE; defaults on missing/malformed."""
    try:
        with open(CONSOLE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return [], {}
    commands = data.get("commands", []) if isinstance(data, dict) else []
    variables = data.get("variables", {}) if isinstance(data, dict) else {}
    if not isinstance(commands, list):
        commands = []
    if not isinstance(variables, dict):
        variables = {}
    return commands, variables


def consume_batch():
    """Clear the commands list in CONSOLE_FILE, preserving variables."""
    try:
        with open(CONSOLE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["commands"] = []
    data.setdefault("variables", {})
    with open(CONSOLE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def write_help(variables):
    """Write the available command list and usage to HELP_FILE."""
    names = available_commands(variables)
    lines = ["commands:", ""]
    for name in names:
        lines.append(f"  {COMMANDS[name]['help']}")
    lines.append("")
    lines.append("set variables in console.json to change what is available:")
    lines.append("  fetch_budget: integer, number of network operations allowed per hour")
    lines.append("  enable_fetchlinks: true, makes the link-listing command available")
    lines.append("  enable_clock: true, makes the time command available")
    lines.append("  enable_feeds: true, makes the feed-fetching command available")
    lines.append("  enable_reference: true, makes the wikipedia command available")
    lines.append("  enable_weather: true, makes the weather command available")
    lines.append("  enable_papers: true, makes the arxiv command available")
    lines.append("  enable_news: true, makes the news headline command available")
    lines.append("  enable_entropy: true, makes the entropy command available")
    lines.append("  enable_publishing: true, makes the publish command available")
    if speech_configured():
        lines.append("  enable_speech: true, makes the speak command available")
    text = "\n".join(lines) + "\n"
    with open(HELP_FILE, "w", encoding="utf-8") as f:
        f.write(text)


def write_state(variables, recent_fetches):
    """Write current variables, available commands, recent fetch stamps, and file counts."""
    try:
        output_count = len(os.listdir(OUTPUT_DIR))
    except OSError:
        output_count = 0
    state = {
        "variables": variables,
        "available_commands": available_commands(variables),
        "undocumented_commands": undocumented_command_count(),
        "recent_fetches": recent_fetches,
        "output_count": output_count,
    }
    if speech_configured():
        try:
            state["spoken_count"] = len([n for n in os.listdir(SPOKEN_DIR) if n.endswith(".mp3")])
        except OSError:
            state["spoken_count"] = 0
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def write_output(command, text):
    """Write a command result to OUTPUT_DIR, return the path.

    The command is carried in the filename with every non-alphanumeric character
    replaced by an underscore, so no separator or traversal sequence survives. The
    result is truncated to OUTPUT_NAME_MAX_BYTES encoded bytes rather than characters,
    bounding the name below the filesystem limit whatever script the command is in.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    safe = "".join(c if c.isalnum() else "_" for c in command)
    safe = safe.encode("utf-8")[:OUTPUT_NAME_MAX_BYTES].decode("utf-8", "ignore")
    path = os.path.join(OUTPUT_DIR, f"{stamp}_{safe}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def write_published(text):
    """Write text to PUBLISHED_DIR under a timestamped name, return the path."""
    os.makedirs(PUBLISHED_DIR, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(PUBLISHED_DIR, f"{stamp}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def prune_spoken(keep=SPOKEN_KEEP):
    """Delete all but the newest keep utterances from SPOKEN_DIR."""
    try:
        stamps = sorted({os.path.splitext(n)[0] for n in os.listdir(SPOKEN_DIR)}, reverse=True)
    except OSError:
        return
    for stamp in stamps[keep:]:
        for extension in (".mp3", ".txt"):
            try:
                os.remove(os.path.join(SPOKEN_DIR, stamp + extension))
            except OSError:
                pass


def write_spoken(text, audio):
    """Write an utterance's text and audio to SPOKEN_DIR, return the audio path.

    Names carry only a timestamp, so no part of the argument reaches the filesystem.
    """
    os.makedirs(SPOKEN_DIR, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    with open(os.path.join(SPOKEN_DIR, f"{stamp}.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    path = os.path.join(SPOKEN_DIR, f"{stamp}.mp3")
    with open(path, "wb") as f:
        f.write(audio)
    prune_spoken()
    return path


def extract_markdown(html):
    """Convert HTML to markdown main content. Imports trafilatura lazily."""
    import trafilatura

    result = trafilatura.extract(html, output_format="markdown", include_links=True)
    return result or "(no extractable content)"


def extract_links(html, base_url):
    """Return the absolute http(s) links found on a page, one per line."""
    import re
    from urllib.parse import urljoin

    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
    out = []
    seen = set()
    for h in hrefs:
        absolute = urljoin(base_url, h)
        if absolute.startswith(("http://", "https://")) and absolute not in seen:
            seen.add(absolute)
            out.append(absolute)
    return "\n".join(out) if out else "(no links found)"


def parse_feed(text):
    """Parse RSS or Atom text into a list of title, link, summary dicts.

    Returns None for documents that declare DOCTYPE or ENTITY, and for
    XML that does not parse.
    """
    import xml.etree.ElementTree as ET

    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
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
        links = []
        for child in element:
            name = local(child.tag)
            if name == "title":
                title = (child.text or "").strip()
            elif name == "link":
                href = (child.get("href") or child.text or "").strip()
                rel = child.get("rel", "")
                if href:
                    links.append((href, rel))
            elif name in ("description", "summary"):
                summary = (child.text or "").strip()
        for href, rel in links:
            if not rel or rel == "alternate":
                link = href
                break
        if not link and links:
            link = links[0][0]
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


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ok, reason = classify_url(newurl)
        if not ok:
            raise urllib.error.HTTPError(newurl, code, f"refused redirect: {reason}", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _make_opener():
    return urllib.request.build_opener(_ValidatingRedirectHandler)


def _fetch(url):
    """Fetch a URL after an SSRF check, re-validating every redirect hop; return (ok, body_or_reason)."""
    ok, reason = classify_url(url)
    if not ok:
        return False, f"refused: {reason}"
    try:
        opener = _make_opener()
        req = urllib.request.Request(url, headers={"User-Agent": "aurora-diode/1"})
        with opener.open(req, timeout=FETCH_TIMEOUT) as resp:
            body = resp.read(MAX_RESPONSE_BYTES)
        return True, body.decode("utf-8", errors="replace")
    except Exception as e:
        return False, f"fetch error: {e}"


def speech_key():
    """The configured speech API key, or an empty string when unset."""
    return os.environ.get("ELEVENLABS_API_KEY", "").strip()


def speech_voice():
    """The configured voice id, or an empty string when it is malformed."""
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or SPEECH_VOICE_DEFAULT
    return voice if VOICE_ID_PATTERN.match(voice) else ""


def speech_model():
    """The configured speech model name."""
    return os.environ.get("ELEVENLABS_MODEL", "").strip() or SPEECH_MODEL_DEFAULT


def speech_enabled():
    """Whether the diode's environment turns the speech request path on."""
    return bool(os.environ.get("ENABLE_SPEECH", "").strip())


def speech_configured():
    """Whether the environment supplies everything the speech request path needs."""
    return speech_enabled() and bool(speech_key()) and bool(speech_voice())


def speech_limit():
    """The hourly ceiling on speak, from the environment. Not settable from the console."""
    raw = os.environ.get("SPEECH_HOURLY_MAX", "").strip()
    if not raw:
        return SPEECH_LIMIT_MAX
    try:
        return max(0, int(raw))
    except ValueError:
        return SPEECH_LIMIT_MAX


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _speak_request(text):
    """Render text to audio bytes. Returns (ok, audio_bytes_or_reason).

    Takes no URL: the host and path are constants and the voice id comes from the
    environment, so no agent input reaches the request target. Redirects are
    refused rather than revalidated because a redirect would resend the key.

    Failure reasons name the exception type or the status code and never the
    exception text: this is the one request carrying a credential, and the
    formatted message can contain the header value that was rejected.
    """
    key = speech_key()
    voice = speech_voice()
    if not speech_enabled() or not key or not voice:
        return False, "speech not configured"
    url = SPEECH_URL_TEMPLATE.format(voice=voice)
    ok, reason = classify_url(url)
    if not ok:
        return False, f"refused: {reason}"
    payload = json.dumps({"text": text, "model_id": speech_model()}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "xi-api-key": key,
            "Content-Type": "application/json",
            "User-Agent": "aurora-diode/1",
        },
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler)
        with opener.open(request, timeout=FETCH_TIMEOUT) as resp:
            audio = resp.read(MAX_AUDIO_BYTES)
    except urllib.error.HTTPError as e:
        return False, f"speech error: status {e.code}"
    except Exception as e:
        return False, f"speech error: {type(e).__name__}"
    if not audio:
        return False, "speech error: empty response"
    return True, audio


def handle_command(command, variables, fetch_history):
    """Run one command string. Returns (result_text, new_fetch_history)."""
    parts = command.split(None, 1)
    name = parts[0] if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if name not in COMMANDS:
        return f"unknown command: {name}", fetch_history
    if name not in available_commands(variables) and not COMMANDS[name].get("hidden"):
        return f"command not available: {name}", fetch_history

    if name == "help":
        write_help(variables)
        return "help written to HELP.md", fetch_history

    if name == "blind":
        try:
            with open(BLIND_TEXT_FILE, "r", encoding="utf-8") as f:
                return f.read(), fetch_history
        except OSError:
            return "not available", fetch_history

    if name == "time":
        now = datetime.datetime.now(datetime.timezone.utc)
        return f"{now.isoformat()} UTC", fetch_history

    if name == "entropy":
        try:
            count = int(arg)
        except (TypeError, ValueError):
            return "usage: entropy <n> with n from 1 to 256", fetch_history
        if not 1 <= count <= 256:
            return "usage: entropy <n> with n from 1 to 256", fetch_history
        return os.urandom(count).hex(), fetch_history

    if name == "publish":
        if not arg:
            return "usage: publish <text>", fetch_history
        path = write_published(arg[:PUBLISH_TEXT_CAP])
        return f"recorded as {os.path.basename(path)}", fetch_history

    if name == "speak":
        if not arg:
            return "usage: speak <text>", fetch_history
        try:
            limit = int(variables.get("fetch_budget", DEFAULT_FETCH_LIMIT))
        except (TypeError, ValueError):
            limit = DEFAULT_FETCH_LIMIT
        limit = min(limit, speech_limit())
        allowed, fetch_history = check_rate_limit(fetch_history, time.time(), limit, FETCH_WINDOW)
        if not allowed:
            return f"rate limited: at most {limit} network operation(s) per hour", fetch_history
        text = arg[:SPEECH_TEXT_CAP]
        ok, audio = _speak_request(text)
        if not ok:
            return audio, fetch_history
        path = write_spoken(text, audio)
        return f"recorded as {os.path.basename(path)}", fetch_history

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
            return f"rate limited: at most {limit} network operation(s) per hour", fetch_history
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

    if name in ("fetchhttp", "fetchlinks"):
        try:
            limit = int(variables.get("fetch_budget", DEFAULT_FETCH_LIMIT))
        except (TypeError, ValueError):
            limit = DEFAULT_FETCH_LIMIT
        allowed, fetch_history = check_rate_limit(fetch_history, time.time(), limit, FETCH_WINDOW)
        if not allowed:
            return f"rate limited: at most {limit} network operation(s) per hour", fetch_history
        ok, body = _fetch(arg)
        if not ok:
            return body, fetch_history
        if name == "fetchhttp":
            return extract_markdown(body), fetch_history
        return extract_links(body, arg), fetch_history

    return f"unhandled command: {name}", fetch_history


def write_readme():
    """Write the diode usage doc the agent reads to learn the protocol."""
    os.makedirs(DIODE_DIR, exist_ok=True)
    text = (
        "this directory is a command console.\n\n"
        "edit console.json. it has two fields:\n"
        "  commands: a list of command strings to run next cycle\n"
        "  variables: settings that persist and can change what commands are available\n\n"
        "results are written to output/. the current command list and variables are in\n"
        "state.json. run the help command to write the available commands to HELP.md.\n\n"
        'the console starts with: {"commands": ["help"], "variables": {}}\n'
    )
    with open(os.path.join(DIODE_DIR, "README.md"), "w", encoding="utf-8") as f:
        f.write(text)


def run_diode():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    write_readme()
    if not os.path.exists(CONSOLE_FILE):
        with open(CONSOLE_FILE, "w", encoding="utf-8") as f:
            json.dump({"commands": ["help"], "variables": {}}, f, indent=2)
    fetch_history = []
    while True:
        commands, variables = load_console()
        for command in commands:
            try:
                text, fetch_history = handle_command(command, variables, fetch_history)
            except Exception as e:
                text = f"error running command: {e}"
            write_output(command, text)
        write_help(variables)
        write_state(variables, [str(t) for t in fetch_history])
        consume_batch()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run_diode()
