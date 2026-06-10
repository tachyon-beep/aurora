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
