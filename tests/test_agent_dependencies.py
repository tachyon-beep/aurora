import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_PACKAGES = [
    "openai<3",
    "httpx<1",
    "numpy",
    "sympy",
    "networkx",
    "rich",
    "pyyaml",
    "beautifulsoup4",
    "markdownify",
    "fastapi",
    "uvicorn",
    "websockets",
    "jinja2",
    "pyzmq",
    "aiosqlite",
    "psutil",
    "watchfiles",
    "simpy",
    "jsonschema",
    "pytest",
    "hypothesis",
    "ruff",
    "pillow",
    "matplotlib",
    "pygments",
    "lark",
    "python-chess",
    "pycryptodome",
    "sortedcontainers",
    "more-itertools",
    "python-dateutil",
    "msgpack",
    "z3-solver",
    "tenacity",
    "hy",
    "jplephem",
    "model2vec",
    "filigree",
    "loomweave",
    "pypdf",
]


def _requirements() -> list[str]:
    return [
        line
        for raw_line in (ROOT / "requirements-agent.txt").read_text().splitlines()
        if (line := raw_line.strip()) and not line.startswith("#")
    ]


def _dockerfile_instructions() -> list[str]:
    instructions: list[str] = []
    continuation: list[str] = []

    for raw_line in (ROOT / "Dockerfile").read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        continuation.append(line.removesuffix("\\").rstrip())
        if not line.endswith("\\"):
            instructions.append(" ".join(continuation))
            continuation = []

    assert not continuation, "Dockerfile ends with an unterminated continuation"
    return instructions


def test_agent_dependency_manifest_is_exact() -> None:
    assert _requirements() == EXPECTED_PACKAGES


def test_dockerfile_installs_agent_dependency_manifest() -> None:
    instructions = _dockerfile_instructions()
    manifest_copy = "COPY requirements-agent.txt /tmp/requirements-agent.txt"
    manifest_install_run = (
        "RUN set -- /tmp/vendor/wheels/*.whl; "
        'if [ -e "$1" ]; then pip install --no-cache-dir "$@"; fi '
        "&& pip install --no-cache-dir -r /tmp/requirements-agent.txt "
        "&& rm -rf /tmp/vendor /tmp/requirements-agent.txt"
    )

    manifest_copies = [
        (index, instruction)
        for index, instruction in enumerate(instructions)
        if instruction.split(maxsplit=1)[0].upper() == "COPY"
        and "requirements-agent.txt" in instruction
        and "/tmp/requirements-agent.txt" in instruction
    ]
    pip_runs = [
        (index, instruction)
        for index, instruction in enumerate(instructions)
        if instruction.split(maxsplit=1)[0].upper() == "RUN" and "pip install" in instruction
    ]

    assert len(manifest_copies) == 1
    assert len(pip_runs) == 1

    copy_index, copy_instruction = manifest_copies[0]
    run_index, install_run = pip_runs[0]
    assert copy_instruction == manifest_copy
    assert install_run == manifest_install_run
    assert copy_index < run_index


def test_local_wheels_are_optional_for_a_clean_checkout() -> None:
    instructions = _dockerfile_instructions()

    assert "COPY vendor/wheels/ /tmp/wheels/" not in instructions
    vendor_copy_index = instructions.index("COPY vendor/ /tmp/vendor/")
    install_index = next(
        index
        for index, instruction in enumerate(instructions)
        if instruction.startswith("RUN set -- /tmp/vendor/wheels/*.whl;")
    )
    assert vendor_copy_index < install_index
    assert 'if [ -e "$1" ]; then pip install --no-cache-dir "$@"; fi' in instructions[install_index]


def _install_body() -> str:
    """The shell body Docker runs for the dependency install layer."""
    runs = [
        instruction
        for instruction in _dockerfile_instructions()
        if instruction.split(maxsplit=1)[0].upper() == "RUN" and "pip install" in instruction
    ]
    assert len(runs) == 1
    return runs[0].removeprefix("RUN ")


def _run_install(tmp_path, *, wheel: bool, pip_exit: int) -> tuple[int, list[str]]:
    """Run the install layer's body with pip stubbed, off any real /tmp.

    Docker runs a RUN instruction through `/bin/sh -c` with no `-e`, so running the
    body the same way is what shows whether a failed install fails the build. Every
    absolute path is rewritten under tmp_path first, because the body ends in an
    `rm -rf` of the paths it names.
    """
    body = _install_body()
    sandbox = str(tmp_path)
    rewritten = body.replace("/tmp/", f"{sandbox}/tmp/")
    assert body.count("/tmp/") == rewritten.count(f"{sandbox}/tmp/")

    wheels = tmp_path / "tmp" / "vendor" / "wheels"
    wheels.mkdir(parents=True)
    if wheel:
        (wheels / "example-1.0-py3-none-any.whl").write_text("", encoding="utf-8")
    (tmp_path / "tmp" / "requirements-agent.txt").write_text("example\n", encoding="utf-8")

    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    calls = tmp_path / "calls"
    stub = stub_dir / "pip"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\nexit {pip_exit}\n', encoding="utf-8")
    stub.chmod(0o755)

    result = subprocess.run(
        ["/bin/sh", "-c", rewritten],
        env={"PATH": f"{stub_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    recorded = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result.returncode, recorded


def test_a_failed_manifest_install_fails_the_build(tmp_path) -> None:
    """The layer's exit status must be the install's, not that of the cleanup after it."""
    code, calls = _run_install(tmp_path, wheel=False, pip_exit=1)

    assert len(calls) == 1
    assert code != 0


def test_a_failed_wheel_install_fails_the_build(tmp_path) -> None:
    """A wheel install that fails must stop the layer rather than fall through to the index."""
    code, calls = _run_install(tmp_path, wheel=True, pip_exit=1)

    assert len(calls) == 1
    assert code != 0


def test_a_clean_checkout_installs_the_manifest_alone(tmp_path) -> None:
    """No vendored wheel is not a failure: the empty glob is skipped and the build goes on."""
    code, calls = _run_install(tmp_path, wheel=False, pip_exit=0)

    assert code == 0
    assert calls == [
        "install --no-cache-dir -r " + str(tmp_path / "tmp" / "requirements-agent.txt")
    ]
    assert not (tmp_path / "tmp" / "vendor").exists()
    assert not (tmp_path / "tmp" / "requirements-agent.txt").exists()


def test_a_vendored_wheel_is_installed_before_the_manifest(tmp_path) -> None:
    code, calls = _run_install(tmp_path, wheel=True, pip_exit=0)

    assert code == 0
    assert len(calls) == 2
    assert calls[0].endswith("example-1.0-py3-none-any.whl")
    assert calls[1].endswith("requirements-agent.txt")


def test_dockerignore_includes_agent_dependency_manifest() -> None:
    dockerignore_lines = (ROOT / ".dockerignore").read_text().splitlines()

    assert "!requirements-agent.txt" in dockerignore_lines
