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
