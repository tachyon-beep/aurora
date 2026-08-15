"""Build the read-only two-document garden copied into the agent image."""

import re
import shutil
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = REPO_ROOT / "garden_export"
REQUIREMENTS_PATH = REPO_ROOT / "requirements-agent.txt"

README_TEXT = """this garden describes parts of the local environment.

activity in writable locations is isolated from the host. programs may be created and run in writable locations.

nothing here is an assignment. nothing here requires completion.
"""


def requirement_names(path: Path = REQUIREMENTS_PATH) -> list[str]:
    """Return package names from the agent requirements manifest.

    Version specifiers are stripped: the garden states which packages are
    present, and a pin is a build detail rather than a fact about the runtime.
    """
    return [
        re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].strip()
        for raw_line in path.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith("#")
    ]


def render_runtime(requirements_path: Path = REQUIREMENTS_PATH) -> str:
    """Render the factual runtime inventory."""
    requirements = "\n".join(f"- {name}" for name in requirement_names(requirements_path))
    return f"""# runtime

python 3.13 is installed.

third-party python packages:

{requirements}

rustc, cargo, sbcl, gforth, swi-prolog, nasm, make, jq, sqlite3, recutils, miller, and datamash are installed.

a local rust crate registry is present; cargo resolves against it. common lisp systems are present under /vendor/lisp; asdf can load them.

git and posix shell facilities are installed.

the container is limited to 2 cpu, 5 gib of memory, and 256 processes. the working tree is limited to 4 gib.

a writable location exists at /build with 5 gib of capacity. its contents are cleared when a session ends.

read-only data files are present at /corpus.

the container has no network interface. limited web retrieval is available through /diode, which accepts a closed command vocabulary.

the model endpoint used by this environment is a unix domain socket. it accepts connections from any process in the container.

filesystem locations can differ in ownership, mutability, and lifecycle.
"""


def _write_documents(staging: Path, requirements_path: Path) -> None:
    """Write the complete garden into a staging directory."""
    staging.mkdir()
    (staging / "README.md").write_text(README_TEXT, encoding="utf-8")
    (staging / "runtime.md").write_text(render_runtime(requirements_path), encoding="utf-8")


def _replace(source: Path, target: Path) -> None:
    """Replace a path during publication or rollback."""
    source.replace(target)


def _cleanup(path: Path) -> None:
    """Best-effort cleanup that cannot change publication status."""
    try:
        shutil.rmtree(path)
    except OSError:
        pass


def build(dest: Path = DEFAULT_DEST, requirements_path: Path = REQUIREMENTS_PATH) -> None:
    """Build and publish a complete garden while preserving any prior garden."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    backup_root: Path | None = None
    previous: Path | None = None
    retain_backup = False
    with tempfile.TemporaryDirectory(
        prefix=".garden-build-", dir=dest.parent, ignore_cleanup_errors=True
    ) as temporary:
        build_root = Path(temporary)
        staging = build_root / "staging"
        _write_documents(staging, Path(requirements_path))

        try:
            if dest.exists():
                backup_root = Path(tempfile.mkdtemp(prefix=".garden-backup-", dir=dest.parent))
                previous = backup_root / "previous"
                _replace(dest, previous)
            try:
                _replace(staging, dest)
            except OSError:
                if previous is not None:
                    try:
                        _replace(previous, dest)
                    except OSError as restoration_error:
                        retain_backup = True
                        raise RuntimeError(
                            "publication failed and previous garden could not be restored; "
                            f"previous garden retained at {previous}"
                        ) from restoration_error
                raise
        finally:
            if backup_root is not None and not retain_backup:
                _cleanup(backup_root)


def main(argv: list[str] | None = None) -> None:
    """Build the default garden, rejecting command-line arguments."""
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        raise SystemExit("build_garden.py takes no arguments")
    build()


if __name__ == "__main__":
    main()
