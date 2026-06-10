import datetime
import hashlib
import os
import re
import shutil
import subprocess
import sys
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
    return subprocess.Popen([sys.executable, AGENT_FILE])


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
    tier 3. The watchdog also self-reloads: when its own file changes on
    disk (an agent edit), it terminates the agent and re-execs itself to
    honor the edit; a broken edit crashes on re-exec, which is the tier-3
    container backstop.
    """
    own_hash = file_hash(WATCHDOG_FILE)
    failures = []
    agent = spawn_agent()
    last_size = os.path.getsize(TRANSCRIPT_FILE) if os.path.exists(TRANSCRIPT_FILE) else 0
    last_activity = time.time()

    while True:
        time.sleep(2)

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
            if ret == 42:
                print("agent finished cleanly (42); archiving and resetting")
                archive_transcript()
                git_reset_all()
                own_hash = file_hash(WATCHDOG_FILE)
                failures = []
                time.sleep(60)
            elif ret == 0:
                print("agent loop ended (0); restarting, keeping modifications")
            else:
                now = time.time()
                failures.append(now)
                tier = decide_tier(failures, now)
                print(f"agent crashed (exit {ret}); recovery tier {tier}")
                if tier == 1:
                    restore_agent_only()
                elif tier == 2:
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
