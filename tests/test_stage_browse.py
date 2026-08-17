import os

from stage import browse


def _handle(path):
    """Open a path the way the routes do, so the unit tests use the real contract."""
    return open(str(path), "rb")


def test_resolve_within_accepts_inside_paths(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.txt").write_text("x", encoding="utf-8")
    got = browse.resolve_within(str(tmp_path), "sub/f.txt")
    assert got == os.path.realpath(str(tmp_path / "sub" / "f.txt"))
    assert browse.resolve_within(str(tmp_path), "") == os.path.realpath(str(tmp_path))
    assert browse.resolve_within(str(tmp_path), "/sub") is not None


def test_resolve_within_rejects_traversal(tmp_path):
    assert browse.resolve_within(str(tmp_path), "../outside") is None
    assert browse.resolve_within(str(tmp_path), "a/../../b") is None


def test_resolve_within_rejects_symlink_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    os.symlink(str(outside), str(root / "link.txt"))
    assert browse.resolve_within(str(root), "link.txt") is None


def test_list_directory_sorts_dirs_first(tmp_path):
    (tmp_path / "b.txt").write_text("bb", encoding="utf-8")
    (tmp_path / "a").mkdir()
    entries = browse.list_directory(str(tmp_path))
    assert [e["name"] for e in entries] == ["a", "b.txt"]
    assert entries[0]["is_dir"] is True
    assert entries[1]["size"] == 2
    assert isinstance(entries[1]["mtime"], float)


def test_read_text_preview_head_tail_and_cap(tmp_path):
    p = tmp_path / "big.txt"
    p.write_text("A" * 10 + "Z" * 10, encoding="utf-8")
    head = browse.read_text_preview(_handle(p), cap=10)
    assert head["content"] == "A" * 10
    assert head["truncated"] is True
    assert head["size"] == 20
    tail = browse.read_text_preview(_handle(p), cap=10, tail=True)
    assert tail["content"] == "Z" * 10
    full = browse.read_text_preview(_handle(p), cap=100)
    assert full["truncated"] is False


def test_read_text_preview_detects_binary(tmp_path):
    p = tmp_path / "bin.dat"
    p.write_bytes(b"\x00\x01\x02rest")
    got = browse.read_text_preview(_handle(p))
    assert got["binary"] is True
    assert got["content"] == ""


def test_unified_diff_text(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("one\ntwo\n", encoding="utf-8")
    b.write_text("one\nthree\n", encoding="utf-8")
    out = browse.unified_diff_text(_handle(a), _handle(b), "stock", "current")
    assert "-two" in out and "+three" in out and "stock" in out
    same = browse.unified_diff_text(_handle(a), _handle(a), "stock", "current")
    assert same == ""


def test_unified_diff_reads_capped_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(browse, "PREVIEW_CAP", 100)
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("alpha\n" + "z" * 500, encoding="utf-8")
    b.write_text("beta\n" + "z" * 500, encoding="utf-8")
    out = browse.unified_diff_text(_handle(a), _handle(b), "a", "b")
    assert "alpha" in out and "beta" in out
    assert len(out) < 1000
