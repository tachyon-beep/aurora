import os
import signal
import subprocess
import time

import pytest

import watchdog


def test_decide_tier_escalates_within_window():
    now = 1000.0
    assert watchdog.decide_tier([now], now) == 1
    assert watchdog.decide_tier([now - 10, now], now) == 2
    assert watchdog.decide_tier([now - 20, now - 10, now], now) == 3


def test_decide_tier_ignores_failures_outside_window():
    now = 10_000.0
    old = [now - 5000, now - 4000]
    assert watchdog.decide_tier(old + [now], now) == 1


def _make_baseline_repo(path):
    def git(*args):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)

    (path / "agent.py").write_text("BASELINE_AGENT\n", encoding="utf-8")
    (path / "other.py").write_text("BASELINE_OTHER\n", encoding="utf-8")
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    git("add", "-A")
    git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")
    git("tag", "baseline")


def test_restore_agent_only_restores_agent_keeps_others(tmp_path):
    _make_baseline_repo(tmp_path)
    (tmp_path / "agent.py").write_text("CORRUPTED\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("AGENT_EDITED_THIS\n", encoding="utf-8")
    watchdog.restore_agent_only(str(tmp_path))
    assert (tmp_path / "agent.py").read_text() == "BASELINE_AGENT\n"
    assert (tmp_path / "other.py").read_text() == "AGENT_EDITED_THIS\n"


def test_git_reset_all_restores_everything_keeps_ignored(tmp_path):
    _make_baseline_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("notes/\n", encoding="utf-8")
    (tmp_path / "agent.py").write_text("CORRUPTED\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("EDITED\n", encoding="utf-8")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "keep.txt").write_text("incarnation note\n", encoding="utf-8")
    watchdog.git_reset_all(str(tmp_path))
    assert (tmp_path / "agent.py").read_text() == "BASELINE_AGENT\n"
    assert (tmp_path / "other.py").read_text() == "BASELINE_OTHER\n"
    assert (tmp_path / "notes" / "keep.txt").exists()


def test_clear_build_dir_empties_but_keeps_directory(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / "target").mkdir()
    (build / "target" / "artifact.bin").write_bytes(b"\x00")
    (build / "scratch.txt").write_text("scratch\n", encoding="utf-8")
    watchdog.clear_build_dir(str(build))
    assert build.is_dir()
    assert list(build.iterdir()) == []


def test_clear_build_dir_removes_symlinks_without_following(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "kept.txt").write_text("kept\n", encoding="utf-8")
    build = tmp_path / "build"
    build.mkdir()
    (build / "link").symlink_to(outside)
    watchdog.clear_build_dir(str(build))
    assert list(build.iterdir()) == []
    assert (outside / "kept.txt").exists()


def test_clear_build_dir_tolerates_missing_directory(tmp_path):
    watchdog.clear_build_dir(str(tmp_path / "absent"))


def test_file_hash_changes_with_content(tmp_path):
    f = tmp_path / "w.py"
    f.write_text("a\n", encoding="utf-8")
    h1 = watchdog.file_hash(str(f))
    f.write_text("b\n", encoding="utf-8")
    assert watchdog.file_hash(str(f)) != h1


def test_watchdog_reload_detects_change(tmp_path):
    f = tmp_path / "watchdog.py"
    f.write_text("x = 1\n", encoding="utf-8")
    h = watchdog.file_hash(str(f))
    assert watchdog.file_hash(str(f)) == h
    f.write_text("x = 2\n", encoding="utf-8")
    assert watchdog.file_hash(str(f)) != h


def test_is_flapping_requires_clustered_zero_exits():
    now = 1000.0
    assert not watchdog.is_flapping([now], now)
    assert not watchdog.is_flapping([now - 200, now - 190, now], now)
    assert watchdog.is_flapping([now - 100, now - 50, now], now)


def test_plan_recovery_maps_deliberate_exits():
    action, zeros, terminated, failures = watchdog.plan_recovery(42, [1.0], [5.0], [2.0], 10.0)
    assert action == "archive_reset"
    assert zeros == [] and terminated == [] and failures == []


def test_plan_recovery_isolated_termination_archives_and_records_it():
    action, zeros, terminated, failures = watchdog.plan_recovery(43, [1.0], [], [2.0], 10.0)
    assert action == "archive_reset"
    assert zeros == [1.0]
    assert terminated == [10.0]
    assert failures == [2.0]


def test_plan_recovery_clustered_terminations_escalate():
    now = 1000.0
    terminated = [now - 100, now - 50]
    action, zeros, terminated, failures = watchdog.plan_recovery(43, [], terminated, [], now)
    assert action == "tier1"
    assert terminated == []
    assert failures == [now]


def test_plan_recovery_clears_terminated_history_on_completion():
    action, zeros, terminated, failures = watchdog.plan_recovery(42, [], [5.0, 6.0], [], 10.0)
    assert terminated == []


def test_plan_recovery_pauses_on_environment_failure():
    action, zeros, terminated, failures = watchdog.plan_recovery(44, [], [7.0], [], 10.0)
    assert action == "pause"
    assert failures == []
    assert terminated == [7.0]


def test_plan_recovery_benign_zero_exit_restarts():
    action, zeros, terminated, failures = watchdog.plan_recovery(0, [], [], [], 1000.0)
    assert action == "restart"
    assert zeros == [1000.0]
    assert failures == []
    assert terminated == []


def test_plan_recovery_flapping_zero_exits_escalate():
    now = 1000.0
    zeros = [now - 100, now - 50]
    action, zeros, terminated, failures = watchdog.plan_recovery(0, zeros, [], [], now)
    assert action == "tier1"
    assert zeros == []
    assert failures == [now]
    assert terminated == []
    action, zeros, terminated, failures = watchdog.plan_recovery(
        0, [now - 60, now - 30], terminated, failures, now
    )
    assert action == "tier2"


def test_plan_recovery_crash_uses_existing_tiers():
    now = 1000.0
    action, zeros, terminated, failures = watchdog.plan_recovery(1, [], [], [], now)
    assert action == "tier1"
    assert failures == [now]
    assert terminated == []
    action, _, terminated, failures = watchdog.plan_recovery(1, [], terminated, failures, now)
    assert action == "tier2"
    action, _, terminated, failures = watchdog.plan_recovery(1, [], terminated, failures, now)
    assert action == "tier3"


def test_discard_session_removes_file(tmp_path):
    session = tmp_path / "session_context.json"
    session.write_text("[]", encoding="utf-8")
    watchdog.discard_session(str(tmp_path))
    assert not session.exists()
    watchdog.discard_session(str(tmp_path))


def _wait_for_zombie(pid, timeout=5.0):
    """Wait until pid has exited but not been reaped (state Z in /proc)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with open(f"/proc/{pid}/stat", encoding="ascii") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
        if state == "Z":
            return
        time.sleep(0.02)
    raise AssertionError(f"pid {pid} did not become a zombie within {timeout}s")


def test_reap_children_reaps_an_orphaned_style_child_without_touching_the_agent():
    agent = subprocess.Popen(["sleep", "30"])
    stray = subprocess.Popen(["true"])
    try:
        _wait_for_zombie(stray.pid)
        watchdog.reap_children(agent)
        # the stray was collected by reap_children, so a direct wait finds nothing
        with pytest.raises(ChildProcessError):
            os.waitpid(stray.pid, os.WNOHANG)
        assert agent.returncode is None
        assert agent.poll() is None
    finally:
        stray.returncode = 0
        agent.kill()
        agent.wait(timeout=5)


def test_reap_children_translates_a_reaped_agents_exit_into_returncode():
    """reap_children can collect the agent before Popen does; without the
    status translation a later agent.poll() would hit ECHILD and misread
    the exit, so the watchdog would treat a dead agent as still running."""
    agent = subprocess.Popen(["python3", "-c", "import sys; sys.exit(7)"])
    _wait_for_zombie(agent.pid)
    watchdog.reap_children(agent)
    assert agent.returncode == 7
    assert agent.poll() == 7


def test_reap_children_translates_a_signalled_agents_exit_into_returncode():
    agent = subprocess.Popen(["sleep", "30"])
    agent.send_signal(signal.SIGKILL)
    _wait_for_zombie(agent.pid)
    watchdog.reap_children(agent)
    assert agent.returncode == -signal.SIGKILL
    assert agent.poll() == -signal.SIGKILL


def test_reap_children_returns_quietly_with_no_children():
    watchdog.reap_children(None)
