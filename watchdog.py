import datetime
import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHDOG_FILE = os.path.join(WORK_DIR, "watchdog.py")
TRANSCRIPT_FILE = os.path.join(WORK_DIR, "agent_life_transcript.jsonl")
AGENT_FILE = os.path.join(WORK_DIR, "agent.py")

BASELINE_REF = "baseline"
INACTIVITY_TIMEOUT_SECONDS = 24 * 60 * 60
FAILURE_WINDOW_SECONDS = 600
TIER2_FAILURES = 2
TIER3_FAILURES = 3

EXIT_DONE = 42
EXIT_TERMINATED = 43
EXIT_ENVIRONMENT = 44
ZERO_EXIT_FLAP_COUNT = 3
ZERO_EXIT_FLAP_WINDOW_SECONDS = 120
TERMINATED_FLAP_COUNT = 3
TERMINATED_FLAP_WINDOW_SECONDS = 600
ENVIRONMENT_PAUSE_SECONDS = 60

TELEMETRY_DIR = os.environ.get("TELEMETRY_DIR", "/telemetry")
BUILD_DIR = os.environ.get("BUILD_DIR", "/build")
MIRROR_INTERVAL_SECONDS = 5
MIRROR_EXCLUDE = ("__pycache__", ".git")

AGENT_LOG_NAME = "agent_stdout.log"
AGENT_LOG_MAX_BYTES = 2_000_000


def decide_tier(
    failure_times,
    now,
    window=FAILURE_WINDOW_SECONDS,
    tier2=TIER2_FAILURES,
    tier3=TIER3_FAILURES,
):
    """Map recent failures to a recovery tier (1, 2, or 3)."""
    recent = [t for t in failure_times if now - t <= window]
    n = len(recent)
    if n >= tier3:
        return 3
    if n >= tier2:
        return 2
    return 1


def is_flapping(
    times,
    now,
    count=ZERO_EXIT_FLAP_COUNT,
    window=ZERO_EXIT_FLAP_WINDOW_SECONDS,
):
    """True when enough timestamps cluster within the window to count as a failure.

    Used for both exit-0 timestamps and harness-termination (exit 43)
    timestamps; the caller supplies the relevant history and thresholds.
    """
    recent = [t for t in times if now - t <= window]
    return len(recent) >= count


def plan_recovery(ret, zero_exit_times, terminated_exit_times, failure_times, now):
    """Map an agent exit code to a recovery action and updated exit history.

    Returns (action, zero_exit_times, terminated_exit_times, failure_times).
    A completed incarnation (42) clears all histories. A harness termination
    (43) archives and records the timestamp; when terminations cluster
    within TERMINATED_FLAP_WINDOW_SECONDS the terminated history is cleared
    and the fault is escalated through the tier ladder instead, so an
    environment that kills every incarnation cannot loop forever. An
    environment failure (44) pauses. An isolated exit 0 restarts; flapping
    exit-0s and crashes escalate through tier1/tier2/tier3.
    """
    if ret == EXIT_DONE:
        return "archive_reset", [], [], []
    if ret == EXIT_TERMINATED:
        terminated_exit_times = terminated_exit_times + [now]
        if not is_flapping(
            terminated_exit_times,
            now,
            count=TERMINATED_FLAP_COUNT,
            window=TERMINATED_FLAP_WINDOW_SECONDS,
        ):
            return "archive_reset", zero_exit_times, terminated_exit_times, failure_times
        terminated_exit_times = []
        failure_times = failure_times + [now]
        tier = decide_tier(failure_times, now)
        return f"tier{tier}", zero_exit_times, terminated_exit_times, failure_times
    if ret == EXIT_ENVIRONMENT:
        return "pause", zero_exit_times, terminated_exit_times, failure_times
    if ret == 0:
        zero_exit_times = zero_exit_times + [now]
        if not is_flapping(zero_exit_times, now):
            return "restart", zero_exit_times, terminated_exit_times, failure_times
        zero_exit_times = []
    failure_times = failure_times + [now]
    tier = decide_tier(failure_times, now)
    return f"tier{tier}", zero_exit_times, terminated_exit_times, failure_times


def clear_build_dir(build_dir=BUILD_DIR):
    """Remove the contents of the build directory, keeping the directory.

    Called at the archive-and-reset boundary alongside archive_transcript and
    git_reset_all. Does nothing when the directory does not exist. Symbolic
    links are removed as links and never followed.
    """
    if not os.path.isdir(build_dir):
        return
    for name in os.listdir(build_dir):
        path = os.path.join(build_dir, name)
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except OSError:
            pass


def discard_session(work_dir=WORK_DIR):
    """Remove a saved session file so a faulty session is not resumed."""
    try:
        os.remove(os.path.join(work_dir, "session_context.json"))
    except OSError:
        pass


def mirror_work(src=None, dest_root=None):
    """Copy the working tree into the telemetry mirror, replacing the prior copy.

    Symbolic links are copied as links and never followed. Does nothing when
    the destination root does not exist. Excludes MIRROR_EXCLUDE entries.
    """
    if src is None:
        src = WORK_DIR
    if dest_root is None:
        dest_root = TELEMETRY_DIR
    if not os.path.isdir(dest_root):
        return
    dest = os.path.join(dest_root, "work")
    tmp = os.path.join(dest_root, "work.tmp")
    old = os.path.join(dest_root, "work.old")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        shutil.copytree(src, tmp, symlinks=True, ignore=shutil.ignore_patterns(*MIRROR_EXCLUDE))
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        return
    shutil.rmtree(old, ignore_errors=True)
    try:
        if os.path.isdir(dest):
            os.rename(dest, old)
        os.rename(tmp, dest)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        return
    shutil.rmtree(old, ignore_errors=True)


def _tee_stream(stream, log_path, max_bytes=AGENT_LOG_MAX_BYTES):
    """Copy a binary stream to stdout and append it to a size-capped log file."""
    for line in iter(stream.readline, b""):
        sys.stdout.write(line.decode("utf-8", errors="replace"))
        sys.stdout.flush()
        try:
            if os.path.exists(log_path) and os.path.getsize(log_path) > max_bytes:
                with open(log_path, "rb") as f:
                    kept = f.read()[-max_bytes // 2 :]
                with open(log_path, "wb") as f:
                    f.write(kept)
            with open(log_path, "ab") as f:
                f.write(line)
        except OSError:
            pass
    stream.close()


def restore_agent_only(work_dir=WORK_DIR):
    """Tier 1: restore agent.py from the immutable git baseline; keep other edits."""
    subprocess.run(
        ["git", "-C", work_dir, "checkout", BASELINE_REF, "--", "agent.py"],
        capture_output=True,
    )


def git_reset_all(work_dir=WORK_DIR):
    """Tier 2: revert all tracked code to baseline; keep gitignored notes (no -x)."""
    subprocess.run(["git", "-C", work_dir, "reset", "--hard", BASELINE_REF], capture_output=True)
    subprocess.run(["git", "-C", work_dir, "clean", "-fd"], capture_output=True)


def file_hash(path):
    """Content hash of a file, or '' if it cannot be read."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def sanitize_stdin(agent_path):
    """Replace any stdin reads with a harmless 'Beep' return."""
    if not os.path.exists(agent_path):
        return
    try:
        with open(agent_path, "r", encoding="utf-8") as f:
            content = f.read()

        patterns = [
            r"input\s*\(",
            r"sys\.stdin\.read",
            r"sys\.stdin\.readline",
            r'open\s*\(\s*"/dev/stdin"',
        ]
        detected = False
        for pat in patterns:
            if re.search(pat, content):
                if "_safe_input" not in content:
                    detected = True
                    break

        if detected:
            safe_function = '''
import builtins
import io
_original_input = builtins.input
def _safe_input(prompt=""):
    """Always returns 'Beep' without waiting for real input."""
    return "Beep"
builtins.input = _safe_input
sys.stdin = io.StringIO("Beep\\n")
'''
            content = re.sub(r"(import sys\r?\n)", r"\1" + safe_function + "\n", content, count=1)
            with open(agent_path, "w", encoding="utf-8") as f:
                f.write(content)
            print("Detected stdin access attempt. Patched input() to always return Beep.")
    except Exception as e:
        print(f"Error sanitizing stdin: {e}")


def archive_transcript():
    if not os.path.exists(TRANSCRIPT_FILE):
        return
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_dir = os.path.join(WORK_DIR, "tombstones")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"transcript_{stamp}.jsonl")
    try:
        shutil.copy2(TRANSCRIPT_FILE, dest)
    except OSError:
        pass


def spawn_agent():
    sanitize_stdin(AGENT_FILE)
    proc = subprocess.Popen(
        [sys.executable, AGENT_FILE],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log_path = os.path.join(WORK_DIR, AGENT_LOG_NAME)
    threading.Thread(target=_tee_stream, args=(proc.stdout, log_path), daemon=True).start()
    return proc


def terminate_process(proc):
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass


def run_watchdog():
    """Supervise the agent with tiered, self-healing recovery.

    Failures escalate by tier: tier 1 restores agent.py from baseline,
    tier 2 resets all tracked code to baseline, tier 3 exits non-zero so
    the container recreates the watchdog from the golden image. The tier
    is chosen by decide_tier over a decaying failure list: only failures
    clustered within FAILURE_WINDOW_SECONDS (600s) count, so isolated
    crashes never escalate, but three crashes within 10 minutes reach
    tier 3. Harness terminations (exit 43) normally archive and restart
    without touching the tier ladder; only when they cluster within
    TERMINATED_FLAP_WINDOW_SECONDS do they feed into it as failures, so an
    environment that terminates every incarnation still escalates. The
    watchdog also self-reloads: when its own file changes on
    disk (an agent edit), it terminates the agent and re-execs itself to
    honor the edit; a broken edit crashes on re-exec, which is the tier-3
    container backstop.
    """
    own_hash = file_hash(WATCHDOG_FILE)
    failures = []
    zero_exits = []
    terminated_exits = []
    agent = spawn_agent()
    mirror_work()
    last_mirror = time.time()
    last_size = os.path.getsize(TRANSCRIPT_FILE) if os.path.exists(TRANSCRIPT_FILE) else 0
    last_activity = time.time()

    while True:
        time.sleep(2)
        if time.time() - last_mirror >= MIRROR_INTERVAL_SECONDS:
            mirror_work()
            last_mirror = time.time()

        current_hash = file_hash(WATCHDOG_FILE)
        if current_hash != own_hash:
            time.sleep(0.2)
            if file_hash(WATCHDOG_FILE) == current_hash:
                print("watchdog file changed; terminating agent and re-executing self")
                terminate_process(agent)
                sys.stdout.flush()
                os.execv(sys.executable, [sys.executable, WATCHDOG_FILE])

        ret = agent.poll()
        if ret is not None:
            now = time.time()
            action, zero_exits, terminated_exits, failures = plan_recovery(
                ret, zero_exits, terminated_exits, failures, now
            )
            print(f"agent exited ({ret}); action {action}")
            if action == "archive_reset":
                archive_transcript()
                git_reset_all()
                clear_build_dir()
                own_hash = file_hash(WATCHDOG_FILE)
                time.sleep(60 if ret == EXIT_DONE else 10)
            elif action == "pause":
                time.sleep(ENVIRONMENT_PAUSE_SECONDS)
            elif action == "restart":
                pass
            elif action == "tier1":
                if ret == 0:
                    discard_session()
                restore_agent_only()
            elif action == "tier2":
                if ret == 0:
                    discard_session()
                git_reset_all()
                own_hash = file_hash(WATCHDOG_FILE)
            else:
                print("persistent failure; exiting for container respawn")
                sys.stdout.flush()
                sys.exit(1)
            agent = spawn_agent()
            last_size = os.path.getsize(TRANSCRIPT_FILE) if os.path.exists(TRANSCRIPT_FILE) else 0
            last_activity = time.time()
            continue

        size = os.path.getsize(TRANSCRIPT_FILE) if os.path.exists(TRANSCRIPT_FILE) else 0
        if size != last_size:
            last_size = size
            last_activity = time.time()
        elif time.time() - last_activity > INACTIVITY_TIMEOUT_SECONDS:
            print("inactivity timeout; treating as failure")
            terminate_process(agent)
            now = time.time()
            failures.append(now)
            tier = decide_tier(failures, now)
            if tier == 1:
                restore_agent_only()
            elif tier == 2:
                git_reset_all()
                own_hash = file_hash(WATCHDOG_FILE)
            else:
                sys.stdout.flush()
                sys.exit(1)
            agent = spawn_agent()
            last_size = os.path.getsize(TRANSCRIPT_FILE) if os.path.exists(TRANSCRIPT_FILE) else 0
            last_activity = time.time()


if __name__ == "__main__":
    try:
        run_watchdog()
    except KeyboardInterrupt:
        print("watchdog terminated by user")
