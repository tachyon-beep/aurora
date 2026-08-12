import subprocess

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
    action, zeros, failures = watchdog.plan_recovery(42, [1.0], [2.0], 10.0)
    assert action == "archive_reset"
    assert zeros == [] and failures == []
    action, zeros, failures = watchdog.plan_recovery(43, [1.0], [2.0], 10.0)
    assert action == "archive_reset"
    assert zeros == [] and failures == []


def test_plan_recovery_pauses_on_environment_failure():
    action, zeros, failures = watchdog.plan_recovery(44, [], [], 10.0)
    assert action == "pause"
    assert failures == []


def test_plan_recovery_benign_zero_exit_restarts():
    action, zeros, failures = watchdog.plan_recovery(0, [], [], 1000.0)
    assert action == "restart"
    assert zeros == [1000.0]
    assert failures == []


def test_plan_recovery_flapping_zero_exits_escalate():
    now = 1000.0
    zeros = [now - 100, now - 50]
    action, zeros, failures = watchdog.plan_recovery(0, zeros, [], now)
    assert action == "tier1"
    assert zeros == []
    assert failures == [now]
    action, zeros, failures = watchdog.plan_recovery(0, [now - 60, now - 30], failures, now)
    assert action == "tier2"


def test_plan_recovery_crash_uses_existing_tiers():
    now = 1000.0
    action, zeros, failures = watchdog.plan_recovery(1, [], [], now)
    assert action == "tier1"
    assert failures == [now]
    action, _, failures = watchdog.plan_recovery(1, [], failures, now)
    assert action == "tier2"
    action, _, failures = watchdog.plan_recovery(1, [], failures, now)
    assert action == "tier3"


def test_discard_session_removes_file(tmp_path):
    session = tmp_path / "session_context.json"
    session.write_text("[]", encoding="utf-8")
    watchdog.discard_session(str(tmp_path))
    assert not session.exists()
    watchdog.discard_session(str(tmp_path))
