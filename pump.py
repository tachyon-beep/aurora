import datetime
import json
import os
import re
import signal
import subprocess
import threading
import time

PUMP_DIR = os.environ.get("PUMP_DIR", "/pump")
ENTRIES_NAME = "entries.json"
STATE_NAME = "state.json"
README_NAME = "README.md"
LOG_DIR_NAME = "log"

POLL_SECONDS = 5
ENTRIES_MAX_BYTES = 262_144
LOG_MAX_BYTES = 1_000_000
REPORTED_NAME_CAP = 80
REPORTED_ERROR_CAP = 200

NAME_PATTERN = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,31}\Z")
MODES = ("once", "interval", "keepalive")
ENTRY_FIELDS = (
    "name",
    "command",
    "mode",
    "at",
    "every_seconds",
    "timeout_seconds",
    "enabled",
    "cwd",
)

COMMAND_MAX_ITEMS = 32
COMMAND_ARG_MAX_BYTES = 4096
INTERVAL_MIN = 10
INTERVAL_MAX = 604_800
TIMEOUT_MIN = 1
TIMEOUT_MAX = 3600
DEFAULT_TIMEOUT_SECONDS = TIMEOUT_MAX
DEFAULT_CWD = PUMP_DIR

BACKOFF_START = 1
BACKOFF_MAX = 300
STABILITY_SECONDS = 60
KILL_GRACE_SECONDS = 5
ORPHAN_POLL_SECONDS = 0.2

MAX_ENTRIES = 32
MAX_CONCURRENT = 8


def _limit(name, default):
    """An integer operator limit from the environment, or the default."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def max_entries():
    """The number of entries served from one entries file."""
    return _limit("PUMP_MAX_ENTRIES", MAX_ENTRIES)


def max_concurrent():
    """The number of entry processes allowed to run at the same time."""
    return _limit("PUMP_MAX_CONCURRENT", MAX_CONCURRENT)


def parse_timestamp(value):
    """Seconds since the epoch for an absolute ISO-8601 string, else None.

    A timestamp without an offset names no instant, so it is not absolute
    and is not accepted.
    """
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.timestamp()


def format_timestamp(epoch):
    """The absolute UTC ISO-8601 form of an epoch value, or None."""
    if epoch is None:
        return None
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat()


def _integer(value, low, high):
    """True when value is a non-boolean integer inside the inclusive range."""
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def validate_entry(entry):
    """Validate one entry object. Returns (parsed, reason).

    parsed is the entry with its defaults filled in and `at` converted to
    seconds since the epoch; reason states why it was rejected. Exactly one
    of the two is None.
    """
    if not isinstance(entry, dict):
        return None, "entry is not an object"
    for field in entry:
        if field not in ENTRY_FIELDS:
            return None, f"unknown field: {field}"
    name = entry.get("name")
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        return None, "invalid entry name"
    mode = entry.get("mode")
    if mode not in MODES:
        return None, "mode must be one of once, interval, keepalive"
    command = entry.get("command")
    if not isinstance(command, list) or not 1 <= len(command) <= COMMAND_MAX_ITEMS:
        return None, f"command must be an array of 1 to {COMMAND_MAX_ITEMS} strings"
    for item in command:
        if not isinstance(item, str):
            return None, f"command must be an array of 1 to {COMMAND_MAX_ITEMS} strings"
        if len(item.encode("utf-8")) > COMMAND_ARG_MAX_BYTES:
            return None, f"command element must be at most {COMMAND_ARG_MAX_BYTES} bytes"
    parsed = {
        "name": name,
        "mode": mode,
        "command": list(command),
        "enabled": True,
        "cwd": DEFAULT_CWD,
    }
    if "enabled" in entry:
        if not isinstance(entry["enabled"], bool):
            return None, "enabled must be a boolean"
        parsed["enabled"] = entry["enabled"]
    if "cwd" in entry:
        cwd = entry["cwd"]
        if not isinstance(cwd, str) or not cwd:
            return None, "cwd must be a non-empty string"
        if len(cwd.encode("utf-8")) > COMMAND_ARG_MAX_BYTES:
            return None, f"cwd must be at most {COMMAND_ARG_MAX_BYTES} bytes"
        parsed["cwd"] = cwd
    if mode == "once":
        if "at" not in entry:
            return None, "mode once requires at"
        at = parse_timestamp(entry["at"])
        if at is None:
            return None, "at must be an absolute iso-8601 timestamp with an offset"
        parsed["at"] = at
    elif "at" in entry:
        return None, "at applies only to mode once"
    if mode == "interval":
        if "every_seconds" not in entry:
            return None, "mode interval requires every_seconds"
        if not _integer(entry["every_seconds"], INTERVAL_MIN, INTERVAL_MAX):
            return None, f"every_seconds must be an integer from {INTERVAL_MIN} to {INTERVAL_MAX}"
        parsed["every_seconds"] = entry["every_seconds"]
    elif "every_seconds" in entry:
        return None, "every_seconds applies only to mode interval"
    if mode == "keepalive":
        if "timeout_seconds" in entry:
            return None, "timeout_seconds does not apply to mode keepalive"
    else:
        timeout = entry.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if not _integer(timeout, TIMEOUT_MIN, TIMEOUT_MAX):
            return None, f"timeout_seconds must be an integer from {TIMEOUT_MIN} to {TIMEOUT_MAX}"
        parsed["timeout_seconds"] = timeout
    return parsed, None


def _reported_name(entry):
    """The name a rejection reports, capped so a junk key cannot bloat the state."""
    name = entry.get("name") if isinstance(entry, dict) else None
    if not isinstance(name, str):
        return ""
    return name[:REPORTED_NAME_CAP]


def evaluate_entries(raw, limit=None):
    """Split raw entries into accepted entries and rejections.

    Entries are considered in file order. Each rejection carries the index
    it had in the file, the name it reported, and the reason, so two entries
    without a usable name are both visible. Entries past the limit are
    rejected rather than dropped.
    """
    if limit is None:
        limit = max_entries()
    accepted = []
    rejected = []
    names = set()
    for index, item in enumerate(raw):
        reported = _reported_name(item)
        if len(accepted) >= limit:
            rejected.append({"index": index, "name": reported, "reason": "entry limit reached"})
            continue
        parsed, reason = validate_entry(item)
        if reason is not None:
            rejected.append({"index": index, "name": reported, "reason": reason})
            continue
        if parsed["name"] in names:
            rejected.append({"index": index, "name": parsed["name"], "reason": "duplicate name"})
            continue
        names.add(parsed["name"])
        accepted.append(parsed)
    return accepted, rejected


def load_entries(path):
    """Read the entries file. Returns (entries, error).

    A missing file is an empty schedule with no error. An unreadable,
    oversized, unparseable, or wrongly-typed file returns no entries and a
    factual reason, so the caller keeps its current schedule rather than
    tearing it down on a torn write.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read(ENTRIES_MAX_BYTES + 1)
    except FileNotFoundError:
        return [], None
    except OSError:
        return None, "entries are not readable"
    if len(raw) > ENTRIES_MAX_BYTES:
        return None, "entries are too large"
    try:
        data = json.loads(raw)
    except ValueError:
        return None, "entries are not valid json"
    if not isinstance(data, list):
        return None, "entries are not an array"
    return data, None


def new_record():
    """The scheduling record of an entry that has never run."""
    return {
        "spent": False,
        "running": False,
        "pid": None,
        "process_start_ticks": None,
        "last_start": None,
        "last_exit": None,
        "last_exit_time": None,
        "terminated_at": None,
        "backoff": 0,
        "last_error": None,
    }


def next_due(entry, record, now):
    """When the entry is next expected to start, or None.

    None means nothing is pending: a spent `once`, or a run already in
    flight.
    """
    if record.get("running"):
        return None
    mode = entry.get("mode")
    if mode == "once":
        return None if record.get("spent") else entry.get("at")
    if mode == "interval":
        last_start = record.get("last_start")
        if last_start is None:
            return now
        return last_start + entry.get("every_seconds", INTERVAL_MIN)
    last_exit_time = record.get("last_exit_time")
    if last_exit_time is None:
        return now
    return last_exit_time + record.get("backoff", 0)


def decide_start(entry, record, now):
    """True when the entry should be started at now."""
    if not entry.get("enabled", True):
        return False
    if record.get("running"):
        return False
    due = next_due(entry, record, now)
    return due is not None and now >= due


def select_starts(entries, records, now, running, limit=None):
    """The entry names to start this cycle, in file order.

    running is the number of entry processes already in flight; the limit
    bounds the two together.
    """
    if limit is None:
        limit = max_concurrent()
    slots = limit - running
    names = []
    for entry in entries:
        if slots <= 0:
            break
        record = records.get(entry["name"], new_record())
        if decide_start(entry, record, now):
            names.append(entry["name"])
            slots -= 1
    return names


def next_backoff(current, run_seconds):
    """The delay before the next start of a keepalive entry.

    A run that stayed up for the stability threshold returns the delay to
    its floor; a shorter run doubles it up to the cap.
    """
    if run_seconds >= STABILITY_SECONDS:
        return BACKOFF_START
    if current < BACKOFF_START:
        return BACKOFF_START
    return min(current * 2, BACKOFF_MAX)


def stop_step(record, now):
    """The next step in stopping a run: "terminate", "kill", or None."""
    terminated_at = record.get("terminated_at")
    if terminated_at is None:
        return "terminate"
    if now - terminated_at >= KILL_GRACE_SECONDS:
        return "kill"
    return None


def kill_step(entry, record, now):
    """The next step in stopping a run that has passed its timeout.

    keepalive entries have no timeout and are never stopped by this.
    """
    if entry.get("mode") == "keepalive":
        return None
    if not record.get("running"):
        return None
    last_start = record.get("last_start")
    if last_start is None:
        return None
    if record.get("terminated_at") is None:
        timeout = entry.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if now - last_start < timeout:
            return None
    return stop_step(record, now)


def append_log(path, data, max_bytes=LOG_MAX_BYTES):
    """Append bytes to a log file, dropping its head once it passes the cap."""
    try:
        if os.path.exists(path) and os.path.getsize(path) > max_bytes:
            with open(path, "rb") as f:
                kept = f.read()[-max_bytes // 2 :]
            with open(path, "wb") as f:
                f.write(kept)
        with open(path, "ab") as f:
            f.write(data)
    except OSError:
        pass


def _drain(stream, log_path, max_bytes=LOG_MAX_BYTES):
    """Append a binary stream to a size-capped log until it ends."""
    for line in iter(stream.readline, b""):
        append_log(log_path, line, max_bytes)
    stream.close()


def start_process(entry, log_dir, max_bytes=LOG_MAX_BYTES):
    """Start an entry's command with its merged output appended to its log.

    The command is an argv list, executed without a shell. The process leads
    its own session, so a stop reaches the whole group rather than only the
    process named in the entry.
    """
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, entry["name"] + ".log")
    proc = subprocess.Popen(
        entry["command"],
        cwd=entry.get("cwd", DEFAULT_CWD),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    thread = threading.Thread(target=_drain, args=(proc.stdout, log_path, max_bytes), daemon=True)
    thread.start()
    return proc


def signal_process(proc, sig):
    """Signal a process group, falling back to the process alone.

    An entry process leads its own session, so the group signal reaches the
    processes it started. A process that shares the pump's own group is
    signalled alone: the group signal would otherwise stop the pump.
    """
    try:
        group = os.getpgid(proc.pid)
        if group != os.getpgrp():
            os.killpg(group, sig)
            return
    except (OSError, TypeError, AttributeError):
        pass
    try:
        proc.send_signal(sig)
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def signal_pid(pid, sig):
    """Signal the group of a process known only by pid, or the process alone.

    The same rule as signal_process: a group the pump itself belongs to is
    never signalled as a group.
    """
    try:
        group = os.getpgid(pid)
        if group != os.getpgrp():
            os.killpg(group, sig)
        else:
            os.kill(pid, sig)
    except OSError:
        pass


def process_identity(pid):
    """(state, start_ticks) of the process at pid from /proc, or None.

    start_ticks is the kernel's start time of the process in clock ticks
    since boot. Together with the pid it names one process: a later process
    that reuses the pid starts later.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            raw = f.read()
    except (OSError, ValueError):
        return None
    _, _, tail = raw.rpartition(b")")
    fields = tail.split()
    if len(fields) < 20:
        return None
    try:
        return fields[0].decode("ascii"), int(fields[19])
    except (UnicodeDecodeError, ValueError):
        return None


def process_alive(pid, start_ticks):
    """True when the process named by (pid, start_ticks) exists and has not exited."""
    identity = process_identity(pid)
    if identity is None:
        return False
    state, ticks = identity
    return ticks == start_ticks and state not in ("Z", "X")


def stop_orphans(orphans, sleep=time.sleep):
    """Stop the processes of a prior pump that are still alive.

    orphans maps an entry name to the (pid, start_ticks) its record carried.
    Each live one is sent SIGTERM as a group, and SIGKILL once the grace
    period passes without it exiting. Returns the names that were signalled.
    """
    live = {name: pair for name, pair in orphans.items() if process_alive(*pair)}
    stopped = list(live)
    for pid, _ in live.values():
        signal_pid(pid, signal.SIGTERM)
    waited = 0.0
    while live and waited < KILL_GRACE_SECONDS:
        sleep(ORPHAN_POLL_SECONDS)
        waited += ORPHAN_POLL_SECONDS
        live = {name: pair for name, pair in live.items() if process_alive(*pair)}
    for pid, _ in live.values():
        signal_pid(pid, signal.SIGKILL)
    return stopped


README_TEXT = """this directory is read by a process scheduler. entries.json describes the
processes it starts. README.md, state.json, and the files under log/ are
written by the scheduler.

entries.json holds a json array of objects. fields:
  name: string matching [a-z0-9][a-z0-9_-]{0,31}
  command: array of 1 to 32 strings, each at most 4096 bytes
  mode: one of once, interval, keepalive
  at: absolute iso-8601 timestamp with an offset; mode once only
  every_seconds: integer from 10 to 604800; mode interval only
  timeout_seconds: integer from 1 to 3600, default 3600; modes once and
    interval only
  enabled: boolean, default true
  cwd: string, default /pump

name, command, and mode are required. command is an argv list and is executed
without a shell.

modes:
  once: the command starts when the current time reaches at, and does not
    start again while the entry keeps that name.
  interval: the command starts when every_seconds have passed since its last
    start. a run still in progress suppresses the next start rather than
    queueing it.
  keepalive: the command starts whenever it is not running, and starts again
    after it exits. the delay before a restart begins at 1 second and doubles
    to a maximum of 300 seconds; a run of 60 seconds or longer returns it to 1
    second.

a run in mode once or interval is sent SIGTERM once timeout_seconds have
passed, and SIGKILL 5 seconds after that. a run in mode keepalive is not
stopped by elapsed time. a run whose entry is removed from entries.json, or
whose entry has enabled false, is stopped the same way. each process leads its
own session, so a signal reaches the processes it started as well. a process
still running from an earlier start of the scheduler is stopped the same way
when the scheduler starts, before its entry is considered again.

an object with an unknown field, an unknown mode, a value outside its bounds, a
name an earlier object already used, or a position past the entry limit is
rejected with a reason. the other objects are unaffected. an entries.json that
cannot be read or parsed leaves the previous set of entries in place.

entries.json is read every 5 seconds, and state.json is rewritten on the same
cycle. state.json carries, for each entry, its last start, its last exit code,
whether it is running, the pid and the start time in clock ticks of a running
process, when it is next due, and its current restart delay,
together with the rejections, the outcome of the last read, and the limits on
the number of entries and on how many run at the same time. the times in it are
absolute and utc.

the merged standard output and standard error of each entry is appended to
log/<name>.log. a log file past 1000000 bytes loses its oldest content.
"""


def write_readme(path):
    """Write the directory's description when it is absent or differs.

    Returns True when a write happened.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            if f.read() == README_TEXT:
                return False
    except (OSError, ValueError):
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(README_TEXT)
    return True


def render_state(entries, records, rejected, status, now):
    """The state.json document: every declared entry, and every rejection."""
    reported = {}
    for entry in entries:
        record = records.get(entry["name"], new_record())
        item = {
            "mode": entry["mode"],
            "enabled": entry.get("enabled", True),
            "running": bool(record.get("running")),
            "last_start": format_timestamp(record.get("last_start")),
            "last_exit": record.get("last_exit"),
            "last_exit_at": format_timestamp(record.get("last_exit_time")),
            "next_due": format_timestamp(next_due(entry, record, now)),
            "backoff_seconds": record.get("backoff", 0),
        }
        if item["running"] and record.get("pid") is not None:
            item["pid"] = record["pid"]
            item["process_start_ticks"] = record.get("process_start_ticks")
        if entry["mode"] == "once":
            item["spent"] = bool(record.get("spent"))
        if "timeout_seconds" in entry:
            item["timeout_seconds"] = entry["timeout_seconds"]
        if record.get("last_error"):
            item["last_error"] = record["last_error"]
        reported[entry["name"]] = item
    return {
        "updated": format_timestamp(now),
        "entries_status": status,
        "max_entries": max_entries(),
        "max_concurrent": max_concurrent(),
        "entries": reported,
        "rejected": list(rejected),
    }


def write_state(path, state):
    """Write the state document via a rename, so a reader never sees a torn file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def restore_records(document):
    """Rebuild scheduling records from a previously written state document.

    A spent `once` entry stays spent across a restart of the pump. Anything
    the document does not carry in the expected shape falls back to the
    record of an entry that has never run, and no entry is restored as
    running: its process did not survive.
    """
    records = {}
    if not isinstance(document, dict):
        return records
    entries = document.get("entries")
    if not isinstance(entries, dict):
        return records
    for name, item in entries.items():
        if not isinstance(name, str) or not isinstance(item, dict):
            continue
        record = new_record()
        record["spent"] = item.get("spent") is True
        record["last_start"] = parse_timestamp(item.get("last_start"))
        record["last_exit_time"] = parse_timestamp(item.get("last_exit_at"))
        exit_code = item.get("last_exit")
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            record["last_exit"] = exit_code
        backoff = item.get("backoff_seconds")
        if isinstance(backoff, (int, float)) and not isinstance(backoff, bool) and backoff >= 0:
            record["backoff"] = min(backoff, BACKOFF_MAX)
        records[name] = record
    return records


def restore_processes(document):
    """The (pid, start_ticks) of each entry a state document reported running.

    Only an entry carrying both integers is returned; these are the processes
    a prior pump may have left behind.
    """
    processes = {}
    if not isinstance(document, dict):
        return processes
    entries = document.get("entries")
    if not isinstance(entries, dict):
        return processes
    for name, item in entries.items():
        if not isinstance(name, str) or not isinstance(item, dict):
            continue
        if item.get("running") is not True:
            continue
        pid = item.get("pid")
        ticks = item.get("process_start_ticks")
        if not _integer(pid, 1, 2**31) or not _integer(ticks, 0, 2**63):
            continue
        processes[name] = (pid, ticks)
    return processes


def read_state(path):
    """The state document last written, or an empty one."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            document = json.load(f)
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def load_records(path):
    """Restore the scheduling records from a state file, or start with none."""
    return restore_records(read_state(path))


class Pump:
    """Runs the entries of a pump directory and reports what they did."""

    def __init__(self, pump_dir=None, clock=time.time, spawn=None, sleep=time.sleep):
        self.dir = pump_dir if pump_dir is not None else PUMP_DIR
        self.entries_file = os.path.join(self.dir, ENTRIES_NAME)
        self.state_file = os.path.join(self.dir, STATE_NAME)
        self.readme_file = os.path.join(self.dir, README_NAME)
        self.log_dir = os.path.join(self.dir, LOG_DIR_NAME)
        self.clock = clock
        self.sleep = sleep
        self.spawn = spawn if spawn is not None else start_process
        self.entries = []
        self.rejected = []
        self.status = "ok"
        self.records = {}
        self.runs = {}
        self.retained = {}

    def prepare(self):
        """Create the directories, publish the README, adopt the prior records.

        A process the prior document reported running, and that is still
        alive, was left behind by a pump that ended without stopping it. It
        is stopped here, so the record it is restored under is true and the
        entry is not started a second time beside it.

        The entries the prior document reported are held as well: until a
        readable entries file replaces them, they are what the state
        document keeps reporting.
        """
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            write_readme(self.readme_file)
        except OSError:
            pass
        document = read_state(self.state_file)
        stop_orphans(restore_processes(document), sleep=self.sleep)
        self.records = restore_records(document)
        retained = document.get("entries")
        self.retained = retained if isinstance(retained, dict) else {}

    def entry(self, name):
        """The declared entry of a name, or None."""
        for entry in self.entries:
            if entry["name"] == name:
                return entry
        return None

    def reload(self):
        """Adopt the entries file, keeping the current set when it cannot be read."""
        raw, error = load_entries(self.entries_file)
        if error is not None:
            self.status = error
            return
        self.entries, self.rejected = evaluate_entries(raw)
        self.status = "ok"
        self.retained = {}

    def collect(self, now):
        """Record the runs that have ended."""
        for name, proc in list(self.runs.items()):
            try:
                code = proc.poll()
                if code is None:
                    continue
                self.runs.pop(name, None)
                record = self.records.setdefault(name, new_record())
                record["running"] = False
                record["pid"] = None
                record["process_start_ticks"] = None
                record["last_exit"] = code
                record["last_exit_time"] = now
                record["terminated_at"] = None
                entry = self.entry(name)
                if entry is not None and entry["mode"] == "keepalive":
                    started = record.get("last_start")
                    elapsed = now - started if started is not None else 0
                    record["backoff"] = next_backoff(record.get("backoff", 0), elapsed)
            except Exception:
                continue

    def enforce(self, now):
        """Stop the runs that have passed their timeout or lost their entry."""
        for name, proc in list(self.runs.items()):
            try:
                record = self.records.setdefault(name, new_record())
                entry = self.entry(name)
                if entry is None or not entry.get("enabled", True):
                    step = stop_step(record, now)
                else:
                    step = kill_step(entry, record, now)
                if step == "terminate":
                    signal_process(proc, signal.SIGTERM)
                    record["terminated_at"] = now
                elif step == "kill":
                    signal_process(proc, signal.SIGKILL)
            except Exception:
                continue

    def launch(self, now):
        """Start the entries that are due, up to the concurrency limit."""
        for name in select_starts(self.entries, self.records, now, len(self.runs)):
            try:
                self.start(self.entry(name), now)
            except Exception:
                continue

    def start(self, entry, now):
        """Start one entry, recording a command that could not be started."""
        name = entry["name"]
        record = self.records.setdefault(name, new_record())
        record["last_start"] = now
        record["terminated_at"] = None
        record["last_error"] = None
        if entry["mode"] == "once":
            record["spent"] = True
        try:
            proc = self.spawn(entry, self.log_dir)
        except Exception as exc:
            record["running"] = False
            record["last_exit"] = None
            record["last_exit_time"] = now
            record["last_error"] = str(exc)[:REPORTED_ERROR_CAP]
            if entry["mode"] == "keepalive":
                record["backoff"] = next_backoff(record.get("backoff", 0), 0)
            return
        record["running"] = True
        pid = getattr(proc, "pid", None)
        identity = process_identity(pid)
        record["pid"] = pid if isinstance(pid, int) else None
        record["process_start_ticks"] = identity[1] if identity is not None else None
        self.runs[name] = proc

    def prune(self):
        """Forget the records of entries that are neither declared nor running."""
        if self.status != "ok":
            return
        names = {entry["name"] for entry in self.entries} | set(self.runs)
        self.records = {name: record for name, record in self.records.items() if name in names}

    def publish(self, now):
        """Rewrite the state document.

        With no schedule to report and an entries file that cannot be read,
        the entries of the prior document are carried forward: what an entry
        has already done outlives a restart taken over an unreadable file.
        """
        try:
            document = render_state(self.entries, self.records, self.rejected, self.status, now)
            if self.status != "ok" and not self.entries and self.retained:
                document["entries"] = self.retained
            write_state(self.state_file, document)
        except OSError:
            pass

    def poll(self):
        """One scheduling cycle."""
        now = self.clock()
        self.reload()
        self.collect(now)
        self.enforce(now)
        self.launch(now)
        self.prune()
        self.publish(now)

    def run_forever(self):
        """Poll until interrupted. A failed cycle is reported and retried."""
        self.prepare()
        while True:
            try:
                self.poll()
            except Exception as exc:
                print(f"poll failed: {exc}", flush=True)
            time.sleep(POLL_SECONDS)


def main():
    Pump().run_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("pump terminated by user")
