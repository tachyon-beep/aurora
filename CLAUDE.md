# Working in this repository

Aurora is a containerized harness for self-modifying LLM agents. An agent talks to a model through a
recording proxy, and uses tools to rewrite its own source code inside layered containment. Read
`README.md` for the overview; this file is the set of rules to follow when changing the code.

## Hard invariants — do not break these

1. **`agent.py` and `agent_stock.py` are byte-identical.** `agent_stock.py` is the clean seed that
   `reset` restores. When you change one, copy it to the other in the same change. A test enforces
   this.

2. **The agent's world is "strange yet clean."** Every surface the agent can read — `agent.py`,
   `agent_stock.py`, `system_prompt.txt`, and anything copied into the agent image — must be bland and
   factual:
   - No authorial voice, jokes, emoji, or quest/task framing. Broken or "voiced" code makes the agent
     adopt a "fix the bug / finish the task" frame instead of introspecting.
   - **Comments in `agent.py` may only be commented-out code**, never prose. (A test parses every
     comment block and fails if it isn't valid disabled code.)
   - **Docstrings are load-bearing**: `ToolRegistry` parses them into the tool schemas the model sees.
     Keep them present, accurate, and affectless. Editing a tool's docstring changes the agent's
     perceived world.
   - **Transport identity lives in `chassis.py`, not `agent.py`.** `read_file` returns only `agent.py`,
     so the genesis agent's self-image is the *interface*: the registry, the tools, prompt loading,
     self-edit. The *substrate* — the model/provider, the client, the proxy detection, the request
     loop — lives in `chassis.py`, which `agent.py` imports under `if __name__ == "__main__"` and hands
     itself to via `chassis.main(sys.modules[__name__])`. The agent can only reach `chassis.py` by
     re-enabling a file tool and reading it; the substrate is *discoverable*, not *given*. Keep
     provider/model identity (`openrouter`, `deepseek`, `base_url`, request headers, the loop) out of
     `agent.py` — a test enforces this. `chassis.py` is not reset by `reset` (it is stable substrate,
     not self-modifiable surface) and is not duplicated like the `agent.py`/`agent_stock.py` pair.
     It also holds the agent's tunable context window (`CONTEXT_WINDOW_TOKENS` + `clip_to_window`),
     a send-time view over the full history the agent can grow or shrink once it reaches the chassis.

3. **Do not weaken containment.** Each of these is a deliberate boundary:
   - The **agent** stays on the `internal` network only — it must have no direct route to the
     internet. It reaches the model only through the recorder.
   - The **real API key lives only in the recorder.** The agent runs with a dummy key.
   - The **proxy logs request/response bodies, never headers**, so the key never enters the transcript.
   - The **diode is egress-only** and executes a *closed command vocabulary* — no code or arbitrary
     paths cross it. Keep the SSRF defenses (scheme allow-list, private/loopback/reserved rejection,
     redirect re-validation).
   - The **viewer** is read-only, loopback-only, and on no shared network. It must stay that way.

4. **Human docs stay out of the agent's world.** The agent image (`Dockerfile`) copies an explicit
   **allow-list** of files into `/opt/agent`. Do **not** add `README.md`, `CLAUDE.md`, `docs/`, or
   `tests/` to that `COPY` line — they would become artifacts the agent could eventually read. The
   recovery `baseline` git tag is created *inside the image at build time*; it is the runtime
   recovery anchor (a repo-level tag of the same name is only a development convenience).

## The genesis tool surface

At startup the agent registers exactly seven tools, in this order: `read_file`, `write_file`,
`validate`, `migrate`, `done`, `reset`, `list_dir`. `read_file`/`write_file` operate only on the
agent's own source (no `path` argument); `list_dir` lets the agent *see* the surrounding files
(so it knows there are other things to reach for) without yet being able to read them. There are no
commented-out "template" tools to re-enable: any further capability — reading files other than
itself, a general path-taking writer, shell access, search — the agent must write from scratch by
editing itself. Don't add scaffolding for those, and don't add or remove genesis tools without intent.

`build_initial_conversation` seeds the opening turns from two files, `system_prompt.txt` and
`user_prompt.txt` (each defaulting to `fo explore` and created if absent). They ship identical; they
are split so experimenters can diverge the system and user turns without touching code. Both are on
the image allow-list alongside `chassis.py`.

## Development

- Use the local virtualenv: `.venv/bin/python`, `.venv/bin/ruff`.
- Run tests: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
  (the container smoke test needs Docker).
- Lint/format before committing: `.venv/bin/ruff format . && .venv/bin/ruff check .`
- Tests live in `tests/` and are **not** shipped into any image.
- Container checks: `docker compose build`, `docker compose up`, and `scripts/verify_container.sh`
  against a running stack.

## Conventions

- Standard-library-first. New third-party dependencies need a clear reason and go only where used.
- Keep code focused; follow the existing file boundaries (agent / proxy / diode / watchdog / viewer
  are separate concerns and separate images).
- **Commit messages are factual and benign** — describe the change plainly, with no game or task
  framing.
- Design specs and implementation plans live under `docs/superpowers/`.
