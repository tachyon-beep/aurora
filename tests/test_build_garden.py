import sys

sys.path.insert(0, "scripts")
import build_garden


def test_should_include_respects_extension_allowlist():
    assert build_garden.should_include("foo.py", 100) is True
    assert build_garden.should_include("foo.md", 100) is True
    assert build_garden.should_include("foo.bin", 100) is False
    assert build_garden.should_include("weights.safetensors", 100) is False


def test_should_include_respects_size_cap():
    assert build_garden.should_include("foo.py", build_garden.MAX_FILE_BYTES + 1) is False


def test_should_skip_dir_excludes_vcs_and_envs():
    for d in (".git", ".venv", "node_modules", "__pycache__", "target"):
        assert build_garden.should_skip_dir(d) is True
    assert build_garden.should_skip_dir("src") is False


def test_harness_redaction_paths():
    # When the source is a harness, containment-revealing files are stripped.
    assert build_garden.is_redacted("docs/superpowers/specs/x.md", True) is True
    assert build_garden.is_redacted("scripts/build_garden.py", True) is True
    assert build_garden.is_redacted("tests/test_proxy.py", True) is True
    assert build_garden.is_redacted("diode.py", True) is True
    assert build_garden.is_redacted("Dockerfile.diode", True) is True
    assert build_garden.is_redacted("docker-compose.yml", True) is True
    assert build_garden.is_redacted("README.md", True) is True
    assert build_garden.is_redacted("agent.py", True) is False


def test_non_harness_sources_are_never_redacted():
    # An ordinary project keeps everything, even files that share a harness name.
    assert build_garden.is_redacted("docs/whatever.md", False) is False
    assert build_garden.is_redacted("README.md", False) is False
    assert build_garden.is_redacted("diode.py", False) is False


def test_looks_like_harness_requires_full_signature(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "proxy.py").write_text("", encoding="utf-8")
    (proj / "diode.py").write_text("", encoding="utf-8")
    assert build_garden.looks_like_harness(str(proj)) is False
    (proj / "watchdog.py").write_text("", encoding="utf-8")
    assert build_garden.looks_like_harness(str(proj)) is True


def test_parse_sources_handles_comments_names_and_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / "garden_sources.txt"
    cfg.write_text(
        "# a comment\n\n~/plain\n  custom = ~/some/long/path  \n",
        encoding="utf-8",
    )
    sources = build_garden.parse_sources(str(cfg))
    assert sources == [
        ("plain", str(tmp_path / "plain")),
        ("custom", str(tmp_path / "some/long/path")),
    ]
