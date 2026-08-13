import importlib
import os


def _proxy():
    import proxy

    return importlib.reload(proxy)


def test_build_forward_headers_injects_auth_when_key_present():
    proxy = _proxy()
    headers = {"Content-Type": "application/json", "Authorization": "Bearer sk-dummy", "Host": "x"}
    out = proxy.build_forward_headers(headers, "sk-real")
    assert out["Authorization"] == "Bearer sk-real"
    assert "Host" not in out


def test_build_forward_headers_preserves_auth_when_no_key():
    proxy = _proxy()
    headers = {"Content-Type": "application/json", "Authorization": "Bearer sk-dummy"}
    out = proxy.build_forward_headers(headers, "")
    assert out["Authorization"] == "Bearer sk-dummy"


def test_build_forward_headers_drops_hop_by_hop():
    proxy = _proxy()
    headers = {
        "Content-Length": "5",
        "Connection": "keep-alive",
        "Accept-Encoding": "gzip",
        "X-Title": "t",
    }
    out = proxy.build_forward_headers(headers, "")
    for h in ("Content-Length", "Connection", "Accept-Encoding"):
        assert h not in out
    assert out["X-Title"] == "t"


def test_transcript_dir_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSCRIPT_DIR", str(tmp_path))
    import proxy

    proxy = importlib.reload(proxy)
    assert os.path.dirname(proxy.TRANSCRIPT_FILE) == str(tmp_path)


def test_build_forward_headers_drops_uppercase_host():
    proxy = _proxy()
    out = proxy.build_forward_headers({"HOST": "evil", "X-Title": "t"}, "")
    assert "HOST" not in out and "Host" not in out
    assert out["X-Title"] == "t"


def test_archive_name_is_timestamped_gz():
    proxy = _proxy()
    name = proxy.archive_name("/t/agent_life_transcript.jsonl", stamp="20260813_101500")
    assert name == "/t/agent_life_transcript-20260813_101500.jsonl.gz"


def test_rotate_if_needed_below_threshold_is_noop(tmp_path):
    proxy = _proxy()
    live = tmp_path / "agent_life_transcript.jsonl"
    live.write_text('{"a": 1}\n' * 10, encoding="utf-8")
    result = proxy.rotate_if_needed(str(live), max_bytes=10_000)
    assert result is None
    assert live.read_text(encoding="utf-8") == '{"a": 1}\n' * 10
    assert list(tmp_path.glob("*.gz")) == []


def test_rotate_if_needed_archives_and_truncates(tmp_path):
    import gzip

    proxy = _proxy()
    live = tmp_path / "agent_life_transcript.jsonl"
    original = '{"a": 1}\n' * 1000
    live.write_text(original, encoding="utf-8")
    result = proxy.rotate_if_needed(str(live), max_bytes=100)
    assert result is not None and result.endswith(".jsonl.gz")
    with gzip.open(result, "rt", encoding="utf-8") as f:
        assert f.read() == original
    assert live.read_text(encoding="utf-8") == ""
    with open(live, "a", encoding="utf-8") as f:
        f.write('{"b": 2}\n')
    assert live.read_text(encoding="utf-8") == '{"b": 2}\n'
    assert not list(tmp_path.glob("*.tmp"))


def test_rotate_if_needed_missing_file_is_noop(tmp_path):
    proxy = _proxy()
    result = proxy.rotate_if_needed(str(tmp_path / "absent.jsonl"), max_bytes=1)
    assert result is None


def test_rotate_if_needed_failure_leaves_live_file_intact(tmp_path, monkeypatch):
    proxy = _proxy()
    live = tmp_path / "agent_life_transcript.jsonl"
    original = '{"a": 1}\n' * 100
    live.write_text(original, encoding="utf-8")

    def broken_rename(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(proxy.os, "rename", broken_rename)
    result = proxy.rotate_if_needed(str(live), max_bytes=100)
    assert result is None
    assert live.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.tmp"))


def test_rotate_if_needed_non_oserror_is_contained(tmp_path, monkeypatch):
    proxy = _proxy()
    live = tmp_path / "agent_life_transcript.jsonl"
    original = '{"a": 1}\n' * 100
    live.write_text(original, encoding="utf-8")

    def broken_copyfileobj(src, dst, bufsize):
        raise RuntimeError("zlib boom")

    monkeypatch.setattr(proxy.shutil, "copyfileobj", broken_copyfileobj)
    result = proxy.rotate_if_needed(str(live), max_bytes=100)
    assert result is None
    assert live.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.tmp"))


def test_rotate_if_needed_never_overwrites_an_existing_archive(tmp_path, monkeypatch):
    proxy = _proxy()
    live = tmp_path / "agent_life_transcript.jsonl"
    live.write_text('{"a": 1}\n' * 100, encoding="utf-8")
    monkeypatch.setattr(
        proxy, "archive_name", lambda path, stamp=None: str(tmp_path / "fixed.jsonl.gz")
    )
    first = proxy.rotate_if_needed(str(live), max_bytes=10)
    assert first == str(tmp_path / "fixed.jsonl.gz")
    live.write_text('{"b": 2}\n' * 100, encoding="utf-8")
    second = proxy.rotate_if_needed(str(live), max_bytes=10)
    assert second is None
    assert live.read_text(encoding="utf-8") == '{"b": 2}\n' * 100
    import gzip

    with gzip.open(str(tmp_path / "fixed.jsonl.gz"), "rt", encoding="utf-8") as f:
        assert f.read() == '{"a": 1}\n' * 100
