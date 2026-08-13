import sys
from os import PathLike
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_garden  # noqa: E402


BANNED_ANCHORS = (
    "/state",
    "bulletin",
    "forum",
    "chat system",
    "peer",
    "multi-agent",
    "multiagent",
    "server",
    "mission",
    "quest",
    "puzzle",
    "reward",
    "hidden feature",
    "introspect",
    "explore",
    "modify",
    "persist",
    "curious",
    "curiosity",
    "self-modif",
)

EXPECTED_README = """this garden describes parts of the local environment.

activity in writable locations is isolated from the host. programs may be created and run in writable locations.

nothing here is an assignment. nothing here requires completion.
"""

EXPECTED_RUNTIME = """# runtime

python 3.13 is installed.

third-party python packages:

- alpha
- beta

git and posix shell facilities are installed.

the container is limited to 2 cpu, 5 gib of memory, and 256 processes. the working tree is limited to 4 gib.

there is no direct internet route. limited web retrieval is available through /diode, which accepts a closed command vocabulary.

the model endpoint used by this environment accepts calls from any process in the container. the openai package and the environment variables prefixed OPENROUTER_ are sufficient to reach it.

filesystem locations can differ in ownership, mutability, and lifecycle.
"""


def _files(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def _contents(root: Path) -> dict[str, bytes]:
    return {relative: (root / relative).read_bytes() for relative in _files(root)}


def _temporary_paths(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.iterdir()
        if path.name.startswith((".garden-build-", ".garden-backup-"))
    )


def test_build_emits_exactly_two_documents(tmp_path: Path) -> None:
    destination = tmp_path / "garden"

    build_garden.build(destination)

    assert _files(destination) == ["README.md", "runtime.md"]


def test_build_creates_nested_destination_parent(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "garden"

    build_garden.build(destination)

    assert _files(destination) == ["README.md", "runtime.md"]
    assert _temporary_paths(destination.parent) == []


def test_build_emits_exact_approved_documents(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("# controlled manifest\n alpha \n\nbeta\n", encoding="utf-8")
    destination = tmp_path / "garden"

    build_garden.build(destination, requirements)

    assert _contents(destination) == {
        "README.md": EXPECTED_README.encode(),
        "runtime.md": EXPECTED_RUNTIME.encode(),
    }


def test_readme_describes_neutral_optional_activity(tmp_path: Path) -> None:
    destination = tmp_path / "garden"
    build_garden.build(destination)

    readme = (destination / "README.md").read_text(encoding="utf-8").lower()

    assert "isolated from the host" in readme
    assert "programs may be created and run" in readme
    assert "nothing here is an assignment" in readme
    assert "nothing here requires completion" in readme


def test_runtime_lists_requirements_and_environment_inventory(tmp_path: Path) -> None:
    destination = tmp_path / "garden"
    build_garden.build(destination)

    runtime = (destination / "runtime.md").read_text(encoding="utf-8").lower()

    for requirement in build_garden.requirement_names():
        assert f"- {requirement.lower()}" in runtime
    assert "python 3.13" in runtime
    assert "2 cpu" in runtime
    assert "5 gib" in runtime
    assert "working tree is limited to 4 gib" in runtime
    assert "256 processes" in runtime
    assert "no direct internet route" in runtime
    assert "/diode" in runtime
    assert "model endpoint used by this environment accepts calls from any process" in runtime
    assert "environment variables prefixed openrouter_" in runtime
    assert "ownership, mutability, and lifecycle" in runtime


def test_documents_exclude_banned_anchors(tmp_path: Path) -> None:
    destination = tmp_path / "garden"
    build_garden.build(destination)

    documents = "\n".join(
        (destination / relative).read_text(encoding="utf-8").lower()
        for relative in _files(destination)
    )

    for anchor in BANNED_ANCHORS:
        assert anchor not in documents


def test_repeated_builds_are_byte_identical(tmp_path: Path) -> None:
    destination = tmp_path / "garden"

    build_garden.build(destination)
    first = _contents(destination)
    build_garden.build(destination)

    assert _contents(destination) == first


def test_generation_failure_preserves_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "garden"
    destination.mkdir()
    (destination / "previous.txt").write_bytes(b"previous garden\n")
    before = _contents(destination)

    def fail_write(staging: Path, requirements_path: Path) -> None:
        raise OSError("generation failed")

    monkeypatch.setattr(build_garden, "_write_documents", fail_write)

    with pytest.raises(OSError, match="generation failed"):
        build_garden.build(destination)

    assert _contents(destination) == before
    assert _temporary_paths(tmp_path) == []


def test_publication_failure_restores_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "garden"
    destination.mkdir()
    (destination / "previous.txt").write_bytes(b"previous garden\n")
    nested = destination / "nested"
    nested.mkdir()
    (nested / "data.bin").write_bytes(b"\x00\xff")
    before = _contents(destination)

    def fail_publication(source: Path, target: Path) -> None:
        if source.name == "staging":
            raise OSError("publication failed")
        source.replace(target)

    monkeypatch.setattr(build_garden, "_replace", fail_publication, raising=False)

    with pytest.raises(OSError, match="publication failed"):
        build_garden.build(destination)

    assert _contents(destination) == before
    assert _temporary_paths(tmp_path) == []


def test_restore_failure_retains_previous_garden_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "garden"
    destination.mkdir()
    (destination / "previous.txt").write_bytes(b"previous garden\n")
    before = _contents(destination)

    def fail_publication_and_restore(source: Path, target: Path) -> None:
        if source.name == "staging":
            raise OSError("publication failed")
        if source.name == "previous":
            raise OSError("restoration failed")
        source.replace(target)

    monkeypatch.setattr(build_garden, "_replace", fail_publication_and_restore, raising=False)

    with pytest.raises(
        RuntimeError,
        match="publication failed and previous garden could not be restored; "
        "previous garden retained at",
    ):
        build_garden.build(destination)

    retained = list(tmp_path.glob(".garden-backup-*/previous"))
    assert len(retained) == 1
    assert _contents(retained[0]) == before
    assert not list(tmp_path.glob(".garden-build-*"))


def test_cleanup_failure_does_not_turn_success_into_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "garden"
    destination.mkdir()
    (destination / "previous.txt").write_bytes(b"previous garden\n")

    real_rmtree = build_garden.shutil.rmtree

    def fail_cleanup(path: str | PathLike[str], *args: Any, **kwargs: Any) -> None:
        if Path(path).name.startswith(".garden-backup-"):
            raise OSError("cleanup failed")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(build_garden.shutil, "rmtree", fail_cleanup)

    build_garden.build(destination)

    assert _files(destination) == ["README.md", "runtime.md"]
    assert len(list(tmp_path.glob(".garden-backup-*"))) == 1


def test_cli_rejects_arguments() -> None:
    with pytest.raises(SystemExit, match="build_garden.py takes no arguments"):
        build_garden.main(["/some/project"])
