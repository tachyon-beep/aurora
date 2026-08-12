import os

import watchdog


def _make_work(tmp_path):
    src = tmp_path / "work"
    src.mkdir()
    (src / "agent.py").write_text("AGENT\n", encoding="utf-8")
    (src / "tombstones").mkdir()
    (src / "tombstones" / "incarnation-1.txt").write_text("note\n", encoding="utf-8")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "junk.pyc").write_text("x", encoding="utf-8")
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("ref\n", encoding="utf-8")
    return src


def test_mirror_copies_tree_and_excludes(tmp_path):
    src = _make_work(tmp_path)
    dest_root = tmp_path / "telemetry"
    dest_root.mkdir()
    watchdog.mirror_work(src=str(src), dest_root=str(dest_root))
    dest = dest_root / "work"
    assert (dest / "agent.py").read_text(encoding="utf-8") == "AGENT\n"
    assert (dest / "tombstones" / "incarnation-1.txt").exists()
    assert not (dest / "__pycache__").exists()
    assert not (dest / ".git").exists()


def test_mirror_reflects_deletions(tmp_path):
    src = _make_work(tmp_path)
    dest_root = tmp_path / "telemetry"
    dest_root.mkdir()
    watchdog.mirror_work(src=str(src), dest_root=str(dest_root))
    (src / "agent.py").unlink()
    watchdog.mirror_work(src=str(src), dest_root=str(dest_root))
    assert not (dest_root / "work" / "agent.py").exists()
    assert (dest_root / "work" / "tombstones").exists()


def test_mirror_does_not_follow_symlinks(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private\n", encoding="utf-8")
    src = _make_work(tmp_path)
    os.symlink(str(secret), str(src / "link.txt"))
    dest_root = tmp_path / "telemetry"
    dest_root.mkdir()
    watchdog.mirror_work(src=str(src), dest_root=str(dest_root))
    copied = dest_root / "work" / "link.txt"
    assert os.path.islink(copied)
    assert not copied.is_file() or os.readlink(copied) == str(secret)


def test_mirror_missing_dest_root_is_a_noop(tmp_path):
    src = _make_work(tmp_path)
    watchdog.mirror_work(src=str(src), dest_root=str(tmp_path / "absent"))
    assert not (tmp_path / "absent").exists()
