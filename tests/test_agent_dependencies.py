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
        'if [ -e "$1" ]; then pip install --no-cache-dir "$@"; fi; '
        "pip install --no-cache-dir -r /tmp/requirements-agent.txt; "
        "rm -rf /tmp/vendor /tmp/requirements-agent.txt"
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


def test_dockerignore_includes_agent_dependency_manifest() -> None:
    dockerignore_lines = (ROOT / ".dockerignore").read_text().splitlines()

    assert "!requirements-agent.txt" in dockerignore_lines
