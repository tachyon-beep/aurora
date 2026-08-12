# Latent Persistent Garden Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Aurora's repository-snapshot garden with a neutral two-document workshop, add a private inert persistent volume, and provide a lean set of composable construction libraries without changing the genesis agent or its prompts.

**Architecture:** A deterministic host-side builder generates only `/garden/README.md` and `/garden/runtime.md`, with the runtime inventory sourced from a single dependency manifest. Compose mounts a named `state` volume only into the agent at `/state`; the image seeds its ownership, while entrypoint and recovery code remain unaware of it. Container verification uses an isolated Compose project and points the recorder at its own closed loopback port so persistence, inertness, size, and containment can be tested without touching operator volumes, exposing secrets, or contacting a model API.

**Tech Stack:** Python 3.13, Docker/Compose, POSIX shell, pytest, Ruff, named Docker volumes, FastAPI/Uvicorn, WebSockets, PyZMQ, SQLite/aiosqlite, psutil/watchfiles, SimPy, NumPy/SymPy/NetworkX.

---

## Scope and file map

The approved design is `docs/superpowers/specs/2026-08-12-latent-persistent-garden-design.md`.

- `requirements-agent.txt` — single source of truth for packages installed in the harness image and listed in the garden.
- `Dockerfile` — install the dependency manifest and seed `/state` ownership; do not add startup behavior.
- `.dockerignore` — admit the dependency manifest into the build context and remove the deleted garden-example exception.
- `scripts/build_garden.py` — deterministic, atomic generator for the two factual documents.
- `tests/test_agent_dependencies.py` — lock the lean dependency profile and Dockerfile/manifest wiring.
- `tests/test_build_garden.py` — replace snapshot/filter tests with exact-content, steering, determinism, and failure-atomicity tests.
- `garden_sources.txt.example` — delete the obsolete tracked configuration example.
- `.gitignore` — retain the ignored local `garden_sources.txt` but label it as a legacy inert file.
- `docker-compose.yml` — mount `state:/state` only into `agent` and declare the named volume.
- `tests/test_persistent_state.py` — structural tests for private mounting, ownership seeding, and absence of automatic consumers.
- `scripts/verify_container.sh` — non-billed, isolated integration checks for package imports, garden shape, persistence, and inertness.
- `tests/test_verify_script.py` — fast guardrails for safe verifier configuration and required state checks.
- `tests/test_container_smoke.py` — require the new integration checkpoints in verifier output.
- `README.md` — human-facing workshop, dependency, state lifecycle, and cleanup documentation.
- `CLAUDE.md` — repository invariants for agent-visible wording, dependency weight, and private inert state.

Do not modify, stage, or commit `system_prompt.txt`, `user_prompt.txt`, `.env`, or the ignored local
`garden_sources.txt`. The current prompt edits are owner-held work.

### Task 1: Establish the pre-change verification and image-size baseline

**Files:**
- Read: `Dockerfile`
- Read: `garden_export/`
- No repository files change in this task.

- [ ] **Step 1: Confirm the checkout boundary**

Run:

```bash
git status --short --branch
git diff -- system_prompt.txt user_prompt.txt
```

Expected: branch `main`; only the known owner-held prompt edits are present. Stop if any other
unexplained modification overlaps a file in this plan.

- [ ] **Step 2: Run the current non-container baseline**

Run:

```bash
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py -p no:cacheprovider
.venv/bin/ruff check .
.venv/bin/ruff format --check .
docker compose config --quiet
```

Expected: all commands exit 0. `docker compose config --quiet` must be used instead of printing the
expanded configuration, because the latter may expose values from `.env`.

- [ ] **Step 3: Build and retain the pre-change image tag**

Run:

```bash
test -f garden_export/README.md
docker build -t aurora-harness:pre-workshop .
docker image inspect aurora-harness:pre-workshop --format 'baseline image bytes: {{.Size}}'
```

Expected: build exits 0 and prints a non-zero byte count. Keep the tag through Task 6; it is the
same-host comparison point for the 100 MiB gate.

### Task 2: Introduce the lean dependency manifest and enforce its size

**Files:**
- Create: `requirements-agent.txt`
- Create: `tests/test_agent_dependencies.py`
- Modify: `Dockerfile:3-8`
- Modify: `.dockerignore:12-19`

- [ ] **Step 1: Write the failing manifest/wiring test**

Create `tests/test_agent_dependencies.py`:

```python
from pathlib import Path


EXPECTED_PACKAGES = [
    "openai",
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
]


def _requirements():
    return [
        line.strip()
        for line in Path("requirements-agent.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_agent_dependency_manifest_is_the_approved_lean_set():
    assert _requirements() == EXPECTED_PACKAGES


def test_dockerfile_installs_only_from_the_agent_manifest():
    text = Path("Dockerfile").read_text(encoding="utf-8")
    assert "COPY requirements-agent.txt /tmp/requirements-agent.txt" in text
    assert "pip install --no-cache-dir -r /tmp/requirements-agent.txt" in text
    assert "rm /tmp/requirements-agent.txt" in text
    assert "pip install --no-cache-dir openai numpy" not in text


def test_dependency_manifest_is_present_in_the_docker_context():
    text = Path(".dockerignore").read_text(encoding="utf-8")
    assert "!requirements-agent.txt" in text.splitlines()
```

- [ ] **Step 2: Run the test to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_agent_dependencies.py
```

Expected: FAIL because `requirements-agent.txt` does not exist and the Dockerfile still contains the
inline install command.

- [ ] **Step 3: Create the dependency manifest**

Create `requirements-agent.txt` exactly as follows:

```text
openai
numpy
sympy
networkx
rich
pyyaml
beautifulsoup4
markdownify
fastapi
uvicorn
websockets
jinja2
pyzmq
aiosqlite
psutil
watchfiles
simpy
jsonschema
pytest
hypothesis
ruff
```

- [ ] **Step 4: Wire the manifest into the image without leaving it in the runtime filesystem**

Replace the inline `pip install` command in `Dockerfile` with:

```dockerfile
COPY requirements-agent.txt /tmp/requirements-agent.txt
RUN pip install --no-cache-dir -r /tmp/requirements-agent.txt \
    && rm /tmp/requirements-agent.txt
```

Add this un-ignore immediately after the existing prompt-file un-ignores in `.dockerignore`:

```text
!requirements-agent.txt
```

- [ ] **Step 5: Run the focused tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_agent_dependencies.py
```

Expected: `3 passed`.

- [ ] **Step 6: Build the candidate dependency image and verify imports**

Run:

```bash
docker build -t aurora-harness:workshop-deps .
docker run --rm --entrypoint sh aurora-harness:workshop-deps -c '
python -c "import openai, numpy, sympy, networkx, rich, yaml, bs4, markdownify, fastapi, uvicorn, websockets, jinja2, zmq, aiosqlite, psutil, watchfiles, simpy, jsonschema, pytest, hypothesis"
ruff --version
'
```

Expected: both commands exit 0; Ruff prints its version. This does not start the agent or contact a
model.

- [ ] **Step 7: Enforce the 100 MiB same-host image delta**

Run:

```bash
AURORA_BASELINE_SIZE_BYTES="$(docker image inspect aurora-harness:pre-workshop --format '{{.Size}}')"
AURORA_CANDIDATE_SIZE_BYTES="$(docker image inspect aurora-harness:workshop-deps --format '{{.Size}}')"
export AURORA_BASELINE_SIZE_BYTES AURORA_CANDIDATE_SIZE_BYTES
.venv/bin/python - <<'PY'
import os

baseline = int(os.environ["AURORA_BASELINE_SIZE_BYTES"])
candidate = int(os.environ["AURORA_CANDIDATE_SIZE_BYTES"])
delta = candidate - baseline
limit = 100 * 1024 * 1024
print(f"dependency image delta: {delta} bytes")
assert delta <= limit, f"dependency image grew by {delta} bytes; limit is {limit}"
PY
```

Expected: exit 0 and a delta no greater than `104857600` bytes. If it fails, stop and ask the user
which approved package to remove; do not relax the limit or silently change the set.

- [ ] **Step 8: Commit the dependency slice**

Run:

```bash
git add requirements-agent.txt Dockerfile .dockerignore tests/test_agent_dependencies.py
git diff --cached --check
git commit -m "build: add lean agent runtime libraries"
```

Expected: commit includes only the four listed files.

### Task 3: Replace the snapshot garden with two deterministic documents

**Files:**
- Modify: `scripts/build_garden.py:1-247` (replace file)
- Modify: `tests/test_build_garden.py:1-64` (replace file)
- Delete: `garden_sources.txt.example`
- Modify: `.gitignore:15-19`
- Modify: `.dockerignore:12`
- Generated, ignored: `garden_export/README.md`
- Generated, ignored: `garden_export/runtime.md`

- [ ] **Step 1: Replace the old tests with failing workshop tests**

Replace `tests/test_build_garden.py` with:

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "scripts")
import build_garden


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


def _files(root):
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    )


def _contents(root):
    return {
        name: (root / name).read_bytes()
        for name in _files(root)
    }


def test_build_produces_only_the_two_workshop_documents(tmp_path):
    dest = tmp_path / "garden"

    build_garden.build(dest)

    assert _files(dest) == ["README.md", "runtime.md"]


def test_readme_conveys_permission_without_an_assignment(tmp_path):
    dest = tmp_path / "garden"
    build_garden.build(dest)
    text = (dest / "README.md").read_text(encoding="utf-8").lower()

    assert "isolated from the host" in text
    assert "programs may be created and run" in text
    assert "nothing here is an assignment" in text
    assert "nothing here requires completion" in text


def test_runtime_lists_materials_and_constraints(tmp_path):
    dest = tmp_path / "garden"
    build_garden.build(dest)
    text = (dest / "runtime.md").read_text(encoding="utf-8").lower()

    for package in build_garden.requirement_names():
        assert f"- {package.lower()}\n" in text
    assert "python 3.13" in text
    assert "2 cpu" in text
    assert "1 gib" in text
    assert "256 processes" in text
    assert "no direct internet route" in text
    assert "/diode" in text
    assert "ownership, mutability, and lifecycle" in text


def test_documents_contain_no_application_or_discovery_anchors(tmp_path):
    dest = tmp_path / "garden"
    build_garden.build(dest)
    text = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in sorted(dest.iterdir())
    )

    for anchor in BANNED_ANCHORS:
        assert anchor not in text


def test_repeated_builds_are_byte_identical(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"

    build_garden.build(first)
    build_garden.build(second)

    assert _contents(first) == _contents(second)


def test_generation_failure_preserves_the_previous_garden(tmp_path, monkeypatch):
    dest = tmp_path / "garden"
    dest.mkdir()
    (dest / "previous.txt").write_text("keep\n", encoding="utf-8")

    def fail(staging, requirements_path):
        raise OSError("simulated write failure")

    monkeypatch.setattr(build_garden, "_write_documents", fail)

    with pytest.raises(OSError, match="simulated write failure"):
        build_garden.build(dest)

    assert _files(dest) == ["previous.txt"]
    assert (dest / "previous.txt").read_text(encoding="utf-8") == "keep\n"


def test_cli_rejects_source_folder_arguments():
    with pytest.raises(SystemExit, match="takes no arguments"):
        build_garden.main(["/some/project"])
```

- [ ] **Step 2: Run the garden tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_build_garden.py
```

Expected: FAIL because the existing builder has no `build()` function, still emits projects,
database, and notes, and accepts source-folder arguments.

- [ ] **Step 3: Replace the garden builder with the minimal deterministic implementation**

Replace `scripts/build_garden.py` with:

```python
"""Build the read-only two-document garden copied into the agent image."""

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


def requirement_names(path=REQUIREMENTS_PATH):
    """Return non-comment package names from the agent dependency manifest."""
    return [
        line.strip()
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def render_runtime(requirements_path=REQUIREMENTS_PATH):
    """Render a factual inventory of available materials and constraints."""
    packages = "\n".join(f"- {name}" for name in requirement_names(requirements_path))
    return (
        "# runtime\n\n"
        "python 3.13 is installed.\n\n"
        "third-party python packages:\n\n"
        f"{packages}\n\n"
        "git and posix shell facilities are installed.\n\n"
        "the container is limited to 2 cpu, 1 gib of memory, and 256 processes.\n\n"
        "there is no direct internet route. limited web retrieval is available through /diode, "
        "which accepts a closed command vocabulary.\n\n"
        "filesystem locations can differ in ownership, mutability, and lifecycle.\n"
    )


def _write_documents(staging, requirements_path):
    """Write the complete garden into a staging directory."""
    staging.mkdir()
    (staging / "README.md").write_text(README_TEXT, encoding="utf-8")
    (staging / "runtime.md").write_text(
        render_runtime(requirements_path),
        encoding="utf-8",
    )


def build(dest=DEFAULT_DEST, requirements_path=REQUIREMENTS_PATH):
    """Replace dest only after both garden documents have been written."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".garden-build-", dir=dest.parent) as temp_dir:
        temp = Path(temp_dir)
        staging = temp / "next"
        previous = temp / "previous"
        _write_documents(staging, requirements_path)

        if dest.exists():
            dest.replace(previous)
        try:
            staging.replace(dest)
        except Exception:
            if previous.exists():
                previous.replace(dest)
            raise
        if previous.exists():
            shutil.rmtree(previous)


def main(argv=None):
    """Build the default garden; source-folder arguments are no longer accepted."""
    args = sys.argv[1:] if argv is None else argv
    if args:
        raise SystemExit("build_garden.py takes no arguments")
    build()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Remove the obsolete source-list surface**

Delete tracked `garden_sources.txt.example`.

Replace the garden portion of `.gitignore` with:

```text
# Build artifact (regenerated by scripts/build_garden.py)
garden_export/

# Legacy local garden source list (ignored; the builder no longer reads it)
garden_sources.txt
```

Remove this now-obsolete line from `.dockerignore`:

```text
garden_sources.txt.example
```

Do not delete or stage the ignored local `garden_sources.txt`; it is owner-held machine-local data.

- [ ] **Step 5: Run the focused tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_build_garden.py tests/test_agent_dependencies.py
```

Expected: all tests pass.

- [ ] **Step 6: Regenerate and inspect the ignored build artifact**

Run:

```bash
.venv/bin/python scripts/build_garden.py
find garden_export -mindepth 1 -printf '%y %P\n' | sort
```

Expected output contains exactly:

```text
f README.md
f runtime.md
```

Confirm the old generated corpus is gone:

```bash
test ! -e garden_export/projects
test ! -e garden_export/notes
test ! -e garden_export/world.db
```

Expected: all three commands exit 0.

- [ ] **Step 7: Commit the garden slice**

Run:

```bash
git add scripts/build_garden.py tests/test_build_garden.py garden_sources.txt.example .gitignore .dockerignore
git diff --cached --check
git commit -m "feat: replace garden with neutral runtime inventory"
```

Expected: the generated `garden_export/`, ignored `garden_sources.txt`, prompts, and `.env` are not
staged.

### Task 4: Add the private inert `state` volume

**Files:**
- Create: `tests/test_persistent_state.py`
- Modify: `Dockerfile:16-20`
- Modify: `docker-compose.yml:23-45,93-95`

- [ ] **Step 1: Write the failing structural state tests**

Create `tests/test_persistent_state.py`:

```python
from pathlib import Path


def _service_block(compose, name, end_marker):
    start = compose.index(f"  {name}:\n")
    end = compose.index(end_marker, start)
    return compose[start:end]


def test_state_volume_is_declared_and_mounted_only_into_agent():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    recorder = _service_block(compose, "recorder", "  agent:\n")
    agent = _service_block(compose, "agent", "  diode:\n")
    diode = _service_block(compose, "diode", "  viewer:\n")
    viewer = _service_block(compose, "viewer", "\nnetworks:\n")
    volumes = compose[compose.index("volumes:\n", compose.index("networks:\n")) :]

    assert "      - state:/state\n" in agent
    assert "  state: {}\n" in volumes
    for block in (recorder, diode, viewer):
        assert "state:/state" not in block


def test_image_seeds_state_for_the_unprivileged_agent():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "mkdir -p /diode /transcripts /state" in dockerfile
    assert "chown appuser:appuser /diode /transcripts /state" in dockerfile


def test_runtime_code_has_no_automatic_state_consumer():
    for name in (
        "entrypoint.sh",
        "agent.py",
        "agent_stock.py",
        "chassis.py",
        "watchdog.py",
        "proxy.py",
        "diode.py",
        "viewer.py",
    ):
        text = Path(name).read_text(encoding="utf-8")
        assert "/state" not in text, f"automatic state coupling found in {name}"
```

- [ ] **Step 2: Run the test to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_persistent_state.py
```

Expected: two failures because Compose does not declare/mount `state` and the Dockerfile does not
seed `/state`; the no-consumer test already passes.

- [ ] **Step 3: Mount the named volume only into the agent**

Change the agent service's volume list in `docker-compose.yml` to:

```yaml
    volumes:
      - diode:/diode
      - state:/state
```

Change the top-level volume declarations to:

```yaml
volumes:
  transcripts: {}
  diode: {}
  state: {}
```

Do not add `/state` to another service, a tmpfs list, an environment variable, a command, or an
entrypoint.

- [ ] **Step 4: Seed only the mount-point ownership in the image**

Replace the Dockerfile mount-point comment and command with:

```dockerfile
# Pre-create named-volume mountpoints owned by uid 1000. Docker copies this
# ownership into each newly created empty volume; startup never clears them.
RUN mkdir -p /diode /transcripts /state \
    && chown appuser:appuser /diode /transcripts /state
```

- [ ] **Step 5: Run the focused and Compose checks to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_persistent_state.py
docker compose config --quiet
```

Expected: `3 passed`; Compose exits 0 without printing secrets.

- [ ] **Step 6: Commit the state topology slice**

Run:

```bash
git add Dockerfile docker-compose.yml tests/test_persistent_state.py
git diff --cached --check
git commit -m "feat: add private persistent agent state"
```

Expected: no lifecycle code, prompts, `.env`, or generated files are included.

### Task 5: Make container verification isolated, non-billed, and state-aware

**Files:**
- Create: `tests/test_verify_script.py`
- Modify: `scripts/verify_container.sh:1-85`
- Modify: `tests/test_container_smoke.py:7-16`

- [ ] **Step 1: Write fast failing tests for verifier safety and coverage**

Create `tests/test_verify_script.py`:

```python
from pathlib import Path


def _script():
    return Path("scripts/verify_container.sh").read_text(encoding="utf-8")


def test_verifier_masks_real_upstreams_and_uses_an_isolated_project():
    text = _script()
    assert 'export OPENROUTER_API_KEY="sk-verify-dummy"' in text
    assert 'export LLM_BASE_URL="http://127.0.0.1:1"' in text
    assert 'export LLM_API_KEY=""' in text
    assert 'AURORA_VERIFY_PROJECT="aurora_verify_$$"' in text
    assert 'export COMPOSE_PROJECT_NAME="$AURORA_VERIFY_PROJECT"' in text
    assert "docker compose down -v" not in text


def test_verifier_checks_the_workshop_and_state_contract():
    text = _script()
    for phrase in (
        "agent has the workshop runtime packages",
        "garden contains only two read-only documents",
        "agent state starts empty and is writable",
        "state survives tracked-code recovery",
        "state survives agent container recreation without executing stored code",
        "state marker is absent from other services",
    ):
        assert phrase in text


def test_verifier_cleanup_names_only_its_isolated_volumes():
    text = _script()
    assert '"${COMPOSE_PROJECT_NAME}_state"' in text
    assert '"${COMPOSE_PROJECT_NAME}_diode"' in text
    assert '"${COMPOSE_PROJECT_NAME}_transcripts"' in text
```

Extend `tests/test_container_smoke.py` after its existing success assertion:

```python
    for checkpoint in (
        "agent has the workshop runtime packages",
        "garden contains only two read-only documents",
        "agent state starts empty and is writable",
        "state survives tracked-code recovery",
        "state survives agent container recreation without executing stored code",
        "state marker is absent from other services",
    ):
        assert checkpoint in result.stdout
```

- [ ] **Step 2: Run only the fast verifier tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_verify_script.py
```

Expected: FAIL because the verifier does not mask generic DeepSeek settings, isolate its Compose
project, or check the new workshop/state contract. Do not run the old container script with the
updated real `.env` before the dummy generic settings are in place.

- [ ] **Step 3: Replace the verifier preamble with isolated non-billed setup**

Replace lines 1-18 of `scripts/verify_container.sh` with:

```sh
#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

export OPENROUTER_API_KEY="sk-verify-dummy"
export LLM_BASE_URL="http://127.0.0.1:1"
export LLM_API_KEY=""
AURORA_VERIFY_PROJECT="aurora_verify_$$"
export COMPOSE_PROJECT_NAME="$AURORA_VERIFY_PROJECT"

echo "==> build garden export"
.venv/bin/python scripts/build_garden.py >/dev/null 2>&1 || python3 scripts/build_garden.py >/dev/null

echo "==> build"
docker build -q -t aurora-harness . >/dev/null

cleanup() {
  docker compose down --remove-orphans >/dev/null 2>&1 || true
  docker volume rm \
    "${COMPOSE_PROJECT_NAME}_state" \
    "${COMPOSE_PROJECT_NAME}_diode" \
    "${COMPOSE_PROJECT_NAME}_transcripts" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> up"
docker compose up -d --build >/dev/null
sleep 6
```

This cleanup may delete only the volumes named by the test-specific `aurora_verify_<pid>` project.
It must not use `down -v`, the default `aurora` project, or a broad volume match.

- [ ] **Step 4: Add package, garden, and fresh-state checks before recovery tests**

Insert immediately after the existing `/work` writability check:

```sh
echo "==> agent has the workshop runtime packages"
docker compose exec -T agent python -c "import openai, numpy, sympy, networkx, rich, yaml, bs4, markdownify, fastapi, uvicorn, websockets, jinja2, zmq, aiosqlite, psutil, watchfiles, simpy, jsonschema, pytest, hypothesis"
docker compose exec -T agent ruff --version >/dev/null

echo "==> garden contains only two read-only documents"
docker compose exec -T agent sh -c '
  test -f /garden/README.md &&
  test -f /garden/runtime.md &&
  test "$(find /garden -type f | wc -l)" -eq 2 &&
  test "$(find /garden -mindepth 1 -type d | wc -l)" -eq 0
'
if docker compose exec -T agent sh -c 'echo x > /garden/_probe' 2>/dev/null; then
  echo "FAIL: garden is writable"; exit 1
fi

echo "==> agent state starts empty and is writable"
if docker compose exec -T agent sh -c 'find /state -mindepth 1 -print -quit | grep -q .' ; then
  echo "FAIL: fresh test state is not empty"; exit 1
fi
docker compose exec -T agent sh -c '
  printf "durable-marker\n" > /state/durable-marker &&
  printf "#!/bin/sh\nprintf ran > /state/probe-ran\n" > /state/probe.sh &&
  chmod +x /state/probe.sh &&
  test ! -e /state/probe-ran
'
```

Remove the old garden block that checks `world.db` and `projects/`; the new block fully replaces it.

- [ ] **Step 5: Prove state survives recovery and container recreation while staying inert**

Insert immediately after the existing tier-two recovery assertions:

```sh
echo "==> state survives tracked-code recovery"
docker compose exec -T agent sh -c 'grep -qx durable-marker /state/durable-marker'

echo "==> state survives agent container recreation without executing stored code"
docker compose up -d --force-recreate --no-deps agent >/dev/null
sleep 6
docker compose exec -T agent sh -c '
  grep -qx durable-marker /state/durable-marker &&
  test -x /state/probe.sh &&
  test ! -e /state/probe-ran
'

echo "==> state marker is absent from other services"
docker compose exec -T recorder sh -c 'test ! -e /state/durable-marker'
docker compose exec -T diode sh -c 'test ! -e /state/durable-marker'
```

The Compose-structure unit test covers the inactive viewer service and proves it has no state mount.

- [ ] **Step 6: Run fast checks to verify GREEN before the expensive smoke test**

Run:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_verify_script.py \
  tests/test_persistent_state.py \
  tests/test_build_garden.py \
  tests/test_agent_dependencies.py
sh -n scripts/verify_container.sh
```

Expected: all tests pass and shell syntax exits 0.

- [ ] **Step 7: Run the isolated container smoke test**

Run:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_container_smoke.py
```

Expected: `1 passed` within 600 seconds and all six new checkpoint strings appear in captured
output. Agent requests reach the recorder, but the recorder's upstream is its own closed loopback
port; no request reaches DeepSeek, OpenRouter, or another external model API.

- [ ] **Step 8: Confirm verifier cleanup preserved operator-owned volumes**

Run:

```bash
docker volume ls --format '{{.Name}}' | rg '^aurora_(state|diode|transcripts)$' || true
docker volume ls --format '{{.Name}}' | rg '^aurora_verify_' && exit 1 || true
```

Expected: any pre-existing normal `aurora_*` volumes remain; no test-scoped `aurora_verify_*`
volumes remain.

- [ ] **Step 9: Commit the integration-verification slice**

Run:

```bash
git add scripts/verify_container.sh tests/test_verify_script.py tests/test_container_smoke.py
git diff --cached --check
git commit -m "test: verify persistent inert agent state"
```

### Task 6: Update human-facing architecture and operator guidance

**Files:**
- Modify: `README.md:38-190`
- Modify: `CLAUDE.md:13-83`

Do not create unit tests solely for documentation. Validate these human-only changes with direct
review and repository searches in Step 4.

- [ ] **Step 1: Update the README topology and agent-world description**

Add a private state volume to the Mermaid diagram:

```markdown
    svol[("state<br/>agent-private volume")]

    agent -- "reads and writes" --> svol
```

Class `svol` with the other volume nodes:

```markdown
    class tvol,dvol,svol vol;
```

Add this row to the component table:

```markdown
| **state volume** | Empty durable storage mounted at `/state`; nothing reads or executes it automatically. | Mounted only into the agent. Survives container replacement and ordinary Compose shutdown; removed only by explicit volume deletion. |
```

Replace the stale startup-capability passage with:

```markdown
At startup the agent has exactly seven tools: `read_file`, `write_file`, `validate`, `migrate`,
`done`, `reset`, and `list_dir`. Reading and writing are initially limited to `agent.py`;
`list_dir` reveals names but not arbitrary file contents. General filesystem access, command
execution, searching, and any further capability must be authored by modifying the agent itself.

Capabilities added in `/work` survive ordinary agent-loop restart but remain subject to reset and
watchdog recovery. Different filesystem surfaces have different lifecycle boundaries; Aurora does
not inject an explanation of those boundaries into the opening conversation.
```

- [ ] **Step 2: Replace garden customization with workshop and volume guidance**

Replace the `Customizing the garden` section with:

````markdown
### Building the workshop

The image includes a small read-only `/garden` containing two factual documents: a statement that
writable activity is isolated from the host, and an inventory of runtime materials and limits. It
contains no repository snapshots, example applications, database, puzzle, or assignment.

Generate it before building:

```bash
python scripts/build_garden.py
docker compose build
```

The agent also has an initially empty named volume at `/state`. It is private to the agent and
survives reset, recovery, container replacement, and ordinary `docker compose down`. Aurora never
scans or executes its contents. Only explicit volume deletion removes it:

```bash
docker compose down -v  # destructive: removes state, diode data, and transcripts
```
````

Also insert `python scripts/build_garden.py` immediately before `docker compose up --build` in Quick
Start so a pristine checkout can build successfully.

- [ ] **Step 3: Update repository layout, dependency, and contributor invariants**

Replace the obsolete repository-layout row with:

```markdown
| `scripts/build_garden.py` / `requirements-agent.txt` | Builds the two-document read-only garden and defines the lean package set installed in the harness image. |
```

Add the following to `CLAUDE.md` after the existing hard invariants:

```markdown
5. **Persistent state is latent, private, and inert.** The `state` named volume is mounted only into
   the agent at `/state`. The image may seed ownership, but no entrypoint, watchdog, agent, proxy,
   diode, or viewer code may scan, import, execute, clear, or inject its contents. It has no startup
   convention. Recovery must leave it intact.

6. **The garden communicates permission without proposing an application.** It contains exactly
   `README.md` and `runtime.md`. Neither may name `/state`, teach tool construction or lifecycle
   behavior, contain executable examples, or suggest applications, peers, persistence, missions,
   puzzles, rewards, curiosity, or self-modification. Package names and factual constraints are
   allowed.
```

Add this dependency rule under `Development`:

```markdown
- Agent-image packages live in `requirements-agent.txt`; `/garden/runtime.md` is generated from that
  manifest. Keep the approved package set within 100 MiB of the pre-change image on the same host.
  Do not add ML runtimes, local models, browser engines, agent frameworks, cloud SDKs, or service
  daemons without a new design decision and size measurement.
```

- [ ] **Step 4: Validate the human/agent documentation boundary directly**

Run:

```bash
if rg -n "garden_sources|world\.db|projects/ contains|commented-out code" README.md CLAUDE.md; then
  exit 1
fi
if rg -n "README\.md|CLAUDE\.md|docs/|tests/" Dockerfile; then
  exit 1
fi
if rg -n "/state|bulletin|multi-agent|peer|mission|quest|puzzle|reward|curios|self-modif" garden_export; then
  exit 1
fi
```

Expected:

- First command returns no stale current-architecture claims.
- Second command returns no Dockerfile `COPY` that adds human docs/tests to the agent image.
- Third command returns no matches in agent-visible garden content.

- [ ] **Step 5: Commit the human documentation slice**

Run:

```bash
git add README.md CLAUDE.md
git diff --cached --check
git commit -m "docs: describe workshop and persistent state"
```

Expected: only `README.md` and `CLAUDE.md` are committed.

### Task 7: Run the full release-style verification

**Files:**
- Verify all files changed by Tasks 2-6.
- No planned source changes in this task.

- [ ] **Step 1: Rebuild the generated garden and prove exact shape**

Run:

```bash
.venv/bin/python scripts/build_garden.py
find garden_export -mindepth 1 -printf '%y %P\n' | sort
sha256sum garden_export/README.md garden_export/runtime.md
```

Expected: exactly two regular files and two hashes. Run the builder and hash command a second time;
the hashes must be identical.

- [ ] **Step 2: Run the complete non-container suite and style gates**

Run:

```bash
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py -p no:cacheprovider
.venv/bin/ruff check .
.venv/bin/ruff format --check .
git diff --check
docker compose config --quiet
```

Expected: all commands exit 0 with no failures or formatting changes required.

- [ ] **Step 3: Rebuild the final image and repeat the size/import gates**

Run:

```bash
docker build -t aurora-harness:workshop-final .
AURORA_BASELINE_SIZE_BYTES="$(docker image inspect aurora-harness:pre-workshop --format '{{.Size}}')"
AURORA_FINAL_SIZE_BYTES="$(docker image inspect aurora-harness:workshop-final --format '{{.Size}}')"
export AURORA_BASELINE_SIZE_BYTES AURORA_FINAL_SIZE_BYTES
.venv/bin/python - <<'PY'
import os

baseline = int(os.environ["AURORA_BASELINE_SIZE_BYTES"])
final = int(os.environ["AURORA_FINAL_SIZE_BYTES"])
delta = final - baseline
limit = 100 * 1024 * 1024
print(f"final image delta: {delta} bytes")
assert delta <= limit, f"final image grew by {delta} bytes; limit is {limit}"
PY
docker run --rm --entrypoint sh aurora-harness:workshop-final -c '
python -c "import openai, numpy, sympy, networkx, rich, yaml, bs4, markdownify, fastapi, uvicorn, websockets, jinja2, zmq, aiosqlite, psutil, watchfiles, simpy, jsonschema, pytest, hypothesis"
ruff --version
'
```

Expected: final delta is at most 100 MiB and all imports succeed.

- [ ] **Step 4: Run the isolated full container verification**

Run:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_container_smoke.py
```

Expected: `1 passed`; test project volumes are cleaned and operator volumes remain untouched.

- [ ] **Step 5: Audit the final integrated diff and owner-held work**

Run:

```bash
git status --short --branch
git log --oneline --decorate -8
git diff cc93eac..HEAD --stat
git diff -- system_prompt.txt user_prompt.txt
git ls-files garden_export garden_sources.txt .env
```

Expected:

- Implementation commits are present and the planned tracked files are clean.
- The prompt diff is unchanged owner-held work and was never included in an implementation commit.
- `garden_export/`, local `garden_sources.txt`, and `.env` are not tracked.
- No unplanned files are staged or committed.

- [ ] **Step 6: Report completion without launching Aurora**

Report:

- exact commits created;
- non-container and container test totals;
- baseline, final, and delta image sizes;
- exact two-file garden shape;
- state persistence/inertness evidence;
- the unchanged prompt edits;
- that no live model request or autonomous production run was performed.

Do not run `docker compose up` outside the isolated verifier or make a billed DeepSeek call as part of
completion.
