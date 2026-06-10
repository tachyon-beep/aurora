import datetime
import ipaddress
import json
import os
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

DIODE_DIR = os.environ.get("DIODE_DIR", "/diode")
CONSOLE_FILE = os.path.join(DIODE_DIR, "console.json")
STATE_FILE = os.path.join(DIODE_DIR, "state.json")
HELP_FILE = os.path.join(DIODE_DIR, "HELP.md")
OUTPUT_DIR = os.path.join(DIODE_DIR, "output")

POLL_SECONDS = 5
FETCH_TIMEOUT = 15
MAX_RESPONSE_BYTES = 2_000_000
DEFAULT_FETCH_LIMIT = 1
FETCH_WINDOW = 3600


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
}


def available_commands(variables):
    """Names of commands whose gate is open under the given variables."""
    return [name for name, spec in COMMANDS.items() if spec["gate"](variables)]


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
    lines.append("  fetch_budget: integer, number of http-fetch calls allowed per hour")
    lines.append("  enable_fetchlinks: true, makes the link-listing command available")
    text = "\n".join(lines) + "\n"
    with open(HELP_FILE, "w", encoding="utf-8") as f:
        f.write(text)


def write_state(variables, recent_fetches):
    """Write current variables, available commands, recent fetch stamps, and output count."""
    try:
        output_count = len(os.listdir(OUTPUT_DIR))
    except OSError:
        output_count = 0
    state = {
        "variables": variables,
        "available_commands": available_commands(variables),
        "recent_fetches": recent_fetches,
        "output_count": output_count,
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def write_output(command, text):
    """Write a command result to OUTPUT_DIR, return the path."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    safe = "".join(c if c.isalnum() else "_" for c in command)[:20]
    path = os.path.join(OUTPUT_DIR, f"{stamp}_{safe}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
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


def handle_command(command, variables, fetch_history):
    """Run one command string. Returns (result_text, new_fetch_history)."""
    parts = command.split(None, 1)
    name = parts[0] if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if name not in COMMANDS:
        return f"unknown command: {name}", fetch_history
    if name not in available_commands(variables):
        return f"command not available: {name}", fetch_history

    if name == "help":
        write_help(variables)
        return "help written to HELP.md", fetch_history

    if name == "time":
        now = datetime.datetime.now(datetime.timezone.utc)
        return f"{now.isoformat()} UTC", fetch_history

    if name in ("fetchhttp", "fetchlinks"):
        try:
            limit = int(variables.get("fetch_budget", DEFAULT_FETCH_LIMIT))
        except (TypeError, ValueError):
            limit = DEFAULT_FETCH_LIMIT
        allowed, fetch_history = check_rate_limit(fetch_history, time.time(), limit, FETCH_WINDOW)
        if not allowed:
            return f"rate limited: at most {limit} fetch(es) per hour", fetch_history
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
