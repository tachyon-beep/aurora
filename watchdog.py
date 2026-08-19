import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHDOG_FILE = os.path.join(WORK_DIR, "watchdog.py")
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

# The liveness signal. The transcript is written by the recorder onto the
# transcripts volume, which this container does not mount, so its size here is
# always zero; the captured agent log is written by this process inside the
# working tree and grows with every line the agent prints.
ACTIVITY_FILE = os.path.join(WORK_DIR, AGENT_LOG_NAME)


def activity_size(path=None):
    """The size of the liveness signal file, or 0 when it does not exist."""
    if path is None:
        path = ACTIVITY_FILE
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


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


def _restore_owner_access(path):
    """Add owner read/write/execute to a directory. Symbolic links are skipped."""
    if os.path.islink(path):
        return
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return
    if not stat.S_ISDIR(mode):
        return
    try:
        os.chmod(path, mode | stat.S_IRWXU)
    except OSError:
        pass


def _force_rmtree(path):
    """Remove a directory tree, restoring owner access where a mode denies it.

    A directory whose mode withholds execute cannot be traversed and one that
    withholds write cannot be emptied, so a plain removal stops there. The
    first pass removes what it can; when anything is left, owner access is
    restored over the remainder top-down and the removal is repeated. A
    symbolic link root is removed as a link and never walked, and links
    inside the tree are removed as links, so no mode outside the tree is
    ever changed and a planted link cannot wedge later replacements.
    Returns True when the tree is gone.
    """
    if os.path.islink(path):
        try:
            os.unlink(path)
        except OSError:
            pass
        return not os.path.lexists(path)
    shutil.rmtree(path, ignore_errors=True)
    if not os.path.lexists(path):
        return True
    _restore_owner_access(path)
    for parent, dirs, _files in os.walk(path):
        for name in dirs:
            _restore_owner_access(os.path.join(parent, name))
    shutil.rmtree(path, ignore_errors=True)
    return not os.path.lexists(path)


def clear_build_dir(build_dir=BUILD_DIR):
    """Remove the contents of the build directory, keeping the directory.

    Called at the archive-and-reset boundary alongside git_reset_all. Does nothing when the directory does not exist. Directory
    modes that deny removal are restored first. Symbolic links are removed as
    links and never followed.
    """
    if not os.path.isdir(build_dir):
        return
    for name in os.listdir(build_dir):
        path = os.path.join(build_dir, name)
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                _force_rmtree(path)
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
    Copied directory modes are reproduced, so the replaced copies are removed
    through _force_rmtree; a mode that denies removal would otherwise leave
    the previous copy in place and stop every later replacement.
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
    _force_rmtree(tmp)
    try:
        shutil.copytree(src, tmp, symlinks=True, ignore=shutil.ignore_patterns(*MIRROR_EXCLUDE))
    except OSError:
        _force_rmtree(tmp)
        return
    _force_rmtree(old)
    try:
        if os.path.isdir(dest):
            os.rename(dest, old)
        os.rename(tmp, dest)
    except OSError:
        _force_rmtree(tmp)
        return
    _force_rmtree(old)


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
            content, applied = re.subn(
                r"(import sys\r?\n)", r"\1" + safe_function + "\n", content, count=1
            )
            if applied:
                with open(agent_path, "w", encoding="utf-8") as f:
                    f.write(content)
                print("Detected stdin access attempt. Patched input() to always return Beep.")
            else:
                print("Detected stdin access attempt; no import sys line to patch.")
    except Exception as e:
        print(f"Error sanitizing stdin: {e}")


def spawn_agent():
    sanitize_stdin(AGENT_FILE)
    proc = subprocess.Popen(
        [sys.executable, AGENT_FILE],
        stdin=subprocess.DEVNULL,
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


def reap_children(agent):
    """Reap exited children so orphaned grandchildren do not become zombies.

    This watchdog is PID 1 (container init). A plain Python init does not reap
    reparented orphans; they stay zombies and eventually exhaust the cgroup pid
    limit. waitpid(-1, WNOHANG) reaps any exited child. If it reaps the managed
    agent, translate the wait status into Popen.returncode so a later
    agent.poll() still reports the exit (otherwise poll() would hit ECHILD and
    wrongly treat the agent as still running).

    The agent is not the only child here. entrypoint.sh starts the scheduler at
    /usr/local/bin/pump.py in a background loop before exec'ing this file, so
    that loop is also a child of PID 1, and processes the scheduler starts
    reparent here when their own parent exits. Both arrive through this
    waitpid, which is why the reaped pid is compared against the agent's rather
    than assumed to be it.
    """
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except OSError:
            return
        if pid == 0:
            return
        if agent is not None and pid == agent.pid and agent.returncode is None:
            if os.WIFSIGNALED(status):
                agent.returncode = -os.WTERMSIG(status)
            elif os.WIFEXITED(status):
                agent.returncode = os.WEXITSTATUS(status)
            else:
                agent.returncode = status


def apply_recovery(action, ret, own_hash):
    """Carry out one recovery action and return the watchdog's own file hash.

    The hash is refreshed after any action that may have rewritten
    watchdog.py from the baseline, so the reload check does not re-exec on
    the harness's own restoration. tier3 exits for a container respawn.
    """
    if action == "archive_reset":
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
    return own_hash


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
    last_size = activity_size()
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

        reap_children(agent)
        ret = agent.poll()
        if ret is not None:
            now = time.time()
            action, zero_exits, terminated_exits, failures = plan_recovery(
                ret, zero_exits, terminated_exits, failures, now
            )
            print(f"agent exited ({ret}); action {action}")
            own_hash = apply_recovery(action, ret, own_hash)
            agent = spawn_agent()
            last_size = activity_size()
            last_activity = time.time()
            continue

        size = activity_size()
        if size != last_size:
            last_size = size
            last_activity = time.time()
        elif time.time() - last_activity > INACTIVITY_TIMEOUT_SECONDS:
            print("inactivity timeout; treating as failure")
            terminate_process(agent)
            ret = agent.poll()
            ret = -1 if ret is None else ret
            now = time.time()
            action, zero_exits, terminated_exits, failures = plan_recovery(
                ret, zero_exits, terminated_exits, failures, now
            )
            print(f"agent stopped after inactivity ({ret}); action {action}")
            own_hash = apply_recovery(action, ret, own_hash)
            agent = spawn_agent()
            last_size = activity_size()
            last_activity = time.time()


if __name__ == "__main__":
    try:
        run_watchdog()
    except KeyboardInterrupt:
        print("watchdog terminated by user")
