# Repository Guidelines

## Project Structure & Module Organization

Aurora is a Python 3.13, Docker Compose harness. Runtime components live at the repository root:
`agent.py` and its byte-identical reset seed `agent_stock.py`, `chassis.py`, `proxy.py`, `diode.py`,
`watchdog.py`, `sense.py`, and `viewer.py`. The broadcast UI and server are under `stage/`; the
static project site and assets are under `site/`. Put maintenance utilities in `scripts/`, pytest
coverage in `tests/`, and dated designs or implementation plans in `docs/superpowers/`. Container boundaries
are defined by `Dockerfile*` and `docker-compose.yml`.

## Build, Test, and Development Commands

- `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py` runs the host test suite.
- `.venv/bin/ruff format . && .venv/bin/ruff check .` formats and lints all Python code.
- `sh scripts/prepare_host.sh && docker compose build` provisions ignored host artifacts, regenerates
  the garden, and builds images.
- `docker compose up` starts the stack; run `scripts/verify_container.sh` against it to check
  containment. The container smoke test requires Docker.

## Coding Style & Naming Conventions

Use four-space indentation, double-quoted strings, and Ruff's 100-character line limit. Name
functions and modules with `snake_case`, classes with `PascalCase`, and constants with
`UPPER_SNAKE_CASE`. Prefer the standard library and keep agent, recorder, diode, watchdog, viewer,
and stage concerns separate. Update `agent.py` and `agent_stock.py` together. Agent tool docstrings
define model-visible schemas; keep them accurate and affectless, and do not add prose comments to
`agent.py`.

## Testing Guidelines

Use pytest files named `tests/test_<area>.py` and test functions named `test_<behaviour>`. Add a
focused regression for every behavior change, then run the host suite. For topology, credential,
or containment changes, also build the images and run the container verifier. The project sets no
numeric coverage threshold; prioritize boundary and failure-path assertions.

## Commit & Pull Request Guidelines

Follow the prevailing `<type>: <imperative summary>` style (`feat:`, `fix:`, `test:`, `docs:`,
`build:`). Keep messages factual and benign. Pull requests should explain the rationale, identify
containment or credential effects, link the issue, and list exact verification commands. Include
screenshots for `stage/` or `site/` changes. Never commit `.env` or expose credentials to the agent.

## Agent Workflow

Use Filigree to claim tracked work atomically. Use Loomweave for code-structure queries, taking
entity IDs from its lookup tools and refreshing a stale index before relying on results.
