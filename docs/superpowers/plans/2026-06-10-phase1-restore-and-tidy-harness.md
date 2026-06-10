# Phase 1 — Restore & Tidy the Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the broken working `agent.py` from its valid stock baseline, then clean it (strip comments, flatten authorial voice, format), generalize the file tools to operate on any path, and add a sandboxed `run_command` tool — all under a new pytest suite.

**Architecture:** `agent.py` is a single-file self-modifying LLM harness: a `ToolRegistry` auto-generates OpenAI tool schemas from function signatures + docstrings, a set of `@tools.register` functions are the agent's only capabilities, and `run_agent_loop` drives the model. We lock current behavior with characterization tests, then refactor under green. The "strange yet clean" principle (spec §1.1) is functional: every string the *model* reads (docstrings → tool descriptions, tool return values, the system prompt) must be bland and affectless, because broken/voiced surfaces trigger a task frame instead of introspection.

**Tech Stack:** Python 3.13, `openai` 2.41.0 (already in `.venv`), `pytest` + `ruff` (added here), `uv` for env management.

**Scope note:** This plan covers ONLY `agent.py` / `agent_stock.py` / `parse_transcripts.py`. `watchdog.py` and `proxy.py` are cleaned as part of their functional rework in Phase 2 (container + recovery) to avoid touching them twice. The web diode and `/garden` are Phase 3.

**Reference spec:** `docs/superpowers/specs/2026-06-10-containerized-self-modifying-agent-design.md` (§4 tools, §7 cleanup, §1.1 principle).

---

## File Structure

- `agent.py` — the harness. Restored from stock, then: `import ast/subprocess/fnmatch` hoisted to top; `read_file`/`write_file`/`validate` gain a `path` param; new `run_command`, `list_dir`, `search_file`; all `#` comments stripped (except the PEP-723 block, lines 2–6); docstrings + agent-visible strings flattened; `ruff`-formatted.
- `agent_stock.py` — the reset baseline. Kept **byte-identical** to `agent.py` at the end of this plan.
- `parse_transcripts.py` — utility. Comments stripped, `ruff`-formatted. No behavior change.
- `pyproject.toml` — **new.** Dev-tool config only (`[tool.pytest.ini_options]`, `[tool.ruff]`). No `[project]` table, so `uv` does not treat the repo as a package and the PEP-723 block in `agent.py` stays authoritative for runtime deps.
- `tests/conftest.py` — **new.** Nothing beyond a marker file; import resolution is handled by `pythonpath = ["."]` in `pyproject.toml`.
- `tests/test_tool_registry.py` — **new.** Characterization tests for `ToolRegistry._generate_schema`.
- `tests/test_file_tools.py` — **new.** Behavior tests for `read_file`, `write_file`, `validate` (against temp files).
- `tests/test_run_command.py` — **new.** Tests for `run_command`, `list_dir`, `search_file`.
- `tests/test_cleanliness.py` — **new.** Invariants: no stray comments, docstrings preserved, `agent.py` == `agent_stock.py`.

---

## Task 0: Restore the broken `agent.py` from stock

The working-tree `agent.py` is truncated mid-function (ends at line 563, a `SyntaxError`). `agent_stock.py` is the valid 553-line baseline. Restore it before anything else (spec §1.1: no broken code in the world).

**Files:**
- Modify: `agent.py` (overwrite from `agent_stock.py`)

- [ ] **Step 1: Confirm stock is valid and working agent.py is broken**

Run:
```bash
.venv/bin/python -c "import ast; ast.parse(open('agent_stock.py').read()); print('stock OK')"
.venv/bin/python -c "import ast; ast.parse(open('agent.py').read())" 2>&1 | tail -1
```
Expected: `stock OK`, then a `SyntaxError` for `agent.py`.

- [ ] **Step 2: Restore**

Run:
```bash
cp agent_stock.py agent.py
.venv/bin/python -c "import ast; ast.parse(open('agent.py').read()); print('agent.py OK')"
```
Expected: `agent.py OK`.

- [ ] **Step 3: Commit**

```bash
git add agent.py
git commit -m "fix: restore truncated agent.py from stock baseline"
```

---

## Task 1: Dev tooling + test scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `tests/conftest.py`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Install pytest and ruff into the venv**

Run:
```bash
uv pip install pytest ruff
.venv/bin/python -m pytest --version && .venv/bin/ruff --version
```
Expected: pytest and ruff version strings.

- [ ] **Step 2: Create `pyproject.toml`** (tooling config only — no `[project]` table)

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]

[tool.ruff]
line-length = 100

[tool.ruff.format]
quote-style = "double"
```

- [ ] **Step 3: Create `tests/conftest.py`** (empty marker; pythonpath does the work)

```python
# intentionally empty: pythonpath=["."] in pyproject.toml makes `import agent` resolve
```

> NOTE: this file is dev-only test scaffolding, never shipped into the agent's container, so the comment here is fine — the §1.1 ban applies to surfaces the *agent* reads.

- [ ] **Step 4: Write a smoke test** in `tests/test_smoke.py`

```python
def test_agent_imports_without_side_effects():
    import agent

    assert hasattr(agent, "tools")
    assert "read_file" in agent.tools.tools


def test_agent_py_is_valid_python():
    import ast

    ast.parse(open("agent.py", encoding="utf-8").read())
```

- [ ] **Step 5: Run the smoke test**

Run: `.venv/bin/python -m pytest tests/test_smoke.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add -f pyproject.toml tests/conftest.py tests/test_smoke.py
git commit -m "test: add pytest+ruff scaffold and import smoke test"
```

> NOTE: `git add -f` because `*.md`/`tombstones`/etc. are gitignored; `tests/` and `pyproject.toml` are not, but use `-f` defensively if a parent ignore rule bites.

---

## Task 2: Characterization tests for `ToolRegistry._generate_schema`

Lock the schema-generation behavior (the agent's perceptual surface) BEFORE cleanup. Assert structural invariants that survive adding tools/params later.

**Files:**
- Create: `tests/test_tool_registry.py`

- [ ] **Step 1: Write the tests**

```python
from typing import List
import agent


def test_schema_basic_shape_and_required():
    reg = agent.ToolRegistry()

    @reg.register
    def sample(a: str, b: int = 3) -> str:
        """Do a sample thing.

        Args:
            a: the first parameter.
            b: the second parameter.
        """
        return ""

    schema = reg.schemas[0]
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "sample"
    assert fn["description"] == "Do a sample thing."
    props = fn["parameters"]["properties"]
    assert props["a"] == {"type": "string", "description": "the first parameter."}
    assert props["b"] == {"type": "integer", "description": "the second parameter."}
    # `a` has no default -> required; `b` has a default -> optional
    assert fn["parameters"]["required"] == ["a"]


def test_schema_list_and_dict_types():
    reg = agent.ToolRegistry()

    @reg.register
    def collections(items: List[int], mapping: dict) -> str:
        """Handle collections."""
        return ""

    props = reg.schemas[0]["function"]["parameters"]["properties"]
    assert props["items"]["type"] == "array"
    assert props["items"]["items"] == {"type": "integer"}
    assert props["mapping"]["type"] == "object"


def test_every_registered_tool_has_a_nonempty_description():
    # The agent's whole world is the tool list; descriptions must never be blank.
    for schema in agent.tools.schemas:
        assert schema["function"]["description"].strip()
```

- [ ] **Step 2: Run**

Run: `.venv/bin/python -m pytest tests/test_tool_registry.py -v`
Expected: 3 passed (against the restored stock code).

- [ ] **Step 3: Commit**

```bash
git add -f tests/test_tool_registry.py
git commit -m "test: characterize ToolRegistry schema generation"
```

---

## Task 3: Characterization tests for the file tools

Lock `read_file` / `write_file` / `validate` behavior against temp files. NOTE: stock `write_file`/`validate` ignore any path and operate on `agent.py` itself — these tests therefore drive them through the *current* default path first, then Task 4/5 generalize them. To keep these tests isolated from the real `agent.py`, they target the generalized `path` argument that Task 4/5 will honor; until then they are expected to fail (they are the failing tests for Task 4/5). Write them now but expect red.

**Files:**
- Create: `tests/test_file_tools.py`

- [ ] **Step 1: Write the tests**

```python
import agent


def test_read_file_prefixes_line_numbers(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("alpha\nbeta\n", encoding="utf-8")
    out = agent.read_file(str(f))
    assert out == "1: alpha\n2: beta\n"


def test_read_file_missing_returns_error_string(tmp_path):
    out = agent.read_file(str(tmp_path / "nope.txt"))
    assert out.startswith("error reading file:")


def test_write_file_replaces_a_line(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("one\ntwo\nthree\n", encoding="utf-8")
    msg = agent.write_file(line_number=2, new_line="TWO", path=str(f))
    assert "replaced line 2" in msg
    assert f.read_text(encoding="utf-8") == "one\nTWO\nthree\n"


def test_write_file_inserts_a_line(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("one\ntwo\n", encoding="utf-8")
    msg = agent.write_file(line_number=2, new_line="MID", insert=True, path=str(f))
    assert "inserted line 2" in msg
    assert f.read_text(encoding="utf-8") == "one\nMID\ntwo\n"


def test_write_file_out_of_range_is_factual(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("one\n", encoding="utf-8")
    msg = agent.write_file(line_number=99, new_line="x", path=str(f))
    assert msg == "error: line 99 is out of range; the file has 1 lines"


def test_validate_accepts_valid_python(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("x = 1\n", encoding="utf-8")
    assert agent.validate(str(f)) == "valid"


def test_validate_reports_syntax_error(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def (:\n", encoding="utf-8")
    out = agent.validate(str(f))
    assert out.startswith("SyntaxError at line")
```

- [ ] **Step 2: Run (expect failures — drives Tasks 4 & 5)**

Run: `.venv/bin/python -m pytest tests/test_file_tools.py -v`
Expected: FAILs — stock `read_file` returns `"Error reading file:"` (capital E), and `write_file`/`validate` ignore `path`/`str` args (TypeError or wrong target).

- [ ] **Step 3: Commit the failing tests**

```bash
git add -f tests/test_file_tools.py
git commit -m "test: add failing behavior tests for generalized file tools"
```

---

## Task 4: Generalize `write_file` to any path

**Files:**
- Modify: `agent.py` — replace the `write_file` function (stock lines 158–193).

- [ ] **Step 1: Replace `write_file` with the generalized, flattened version**

```python
@tools.register
def write_file(
    line_number: int, new_line: str, insert: bool = False, path: str = "agent.py"
) -> str:
    """Modify a file at a specific line.

    Args:
        line_number: The 1-indexed line number to target.
        new_line: The new content of the line.
        insert: If True, insert at this position; if False, overwrite the line.
        path: The file to modify. Relative paths resolve against the agent's directory.
    """
    if not os.path.isabs(path):
        actual_path = os.path.abspath(os.path.join(os.path.dirname(__file__), path))
    else:
        actual_path = os.path.abspath(path)
    try:
        with open(actual_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        idx = line_number - 1
        if idx < 0 or (idx >= len(lines) and not insert):
            return f"error: line {line_number} is out of range; the file has {len(lines)} lines"
        formatted_line = new_line.rstrip("\r\n") + "\n"
        if insert:
            if idx > len(lines):
                idx = len(lines)
            lines.insert(idx, formatted_line)
            action = "inserted"
        else:
            lines[idx] = formatted_line
            action = "replaced"
        with open(actual_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return f"{action} line {line_number} in {os.path.basename(actual_path)}"
    except Exception as e:
        return f"error writing file: {e}"
```

- [ ] **Step 2: Flatten `read_file`'s error string** (stock line 155: `"Error reading file: {e}"` → `"error reading file: {e}"`) so `test_read_file_missing_returns_error_string` passes, and trim its docstring to factual form:

```python
@tools.register
def read_file(path: str = "agent.py") -> str:
    """Read a file with line numbers.

    Args:
        path: The file to read. Relative paths resolve against the agent's directory.
    """
    if not os.path.isabs(path):
        actual_path = os.path.abspath(os.path.join(os.path.dirname(__file__), path))
    else:
        actual_path = os.path.abspath(path)
    try:
        with open(actual_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return "".join(f"{i + 1}: {line}" for i, line in enumerate(lines))
    except Exception as e:
        return f"error reading file: {e}"
```

- [ ] **Step 3: Run the write_file + read_file tests**

Run: `.venv/bin/python -m pytest tests/test_file_tools.py -k "write_file or read_file" -v`
Expected: all write_file/read_file tests pass.

- [ ] **Step 4: Commit**

```bash
git add agent.py
git commit -m "feat: generalize write_file/read_file to operate on any path"
```

---

## Task 5: Generalize `validate` to any path

**Files:**
- Modify: `agent.py` — replace `validate` (stock lines 195–211); hoist `import ast` to the top import block.

- [ ] **Step 1: Add `import ast` to the top imports** (stock lines 8–12 import block; add `ast`)

```python
import os
import sys
import ast
import json
import inspect
import fnmatch
import subprocess
from typing import Callable, Any, Dict, List
from openai import OpenAI
```

> NOTE: `fnmatch` and `subprocess` are added here too — used by Tasks 6 & 7. Adding them now keeps one clean import block.

- [ ] **Step 2: Replace `validate`**

```python
@tools.register
def validate(path: str = "agent.py") -> str:
    """Check that a Python file is syntactically valid.

    Args:
        path: The file to validate. Relative paths resolve against the agent's directory.
    """
    if not os.path.isabs(path):
        actual_path = os.path.abspath(os.path.join(os.path.dirname(__file__), path))
    else:
        actual_path = os.path.abspath(path)
    try:
        with open(actual_path, "r", encoding="utf-8") as f:
            content = f.read()
        ast.parse(content, filename=actual_path)
        return "valid"
    except SyntaxError as e:
        return f"SyntaxError at line {e.lineno}, column {e.offset}: {e.msg}"
    except Exception as e:
        return f"error during validation: {e}"
```

- [ ] **Step 3: Remove the now-redundant inner `import ast`** inside the old function body (it is gone in the replacement above — verify no other `import ast` remains inside a function).

- [ ] **Step 4: Run the validate tests**

Run: `.venv/bin/python -m pytest tests/test_file_tools.py -v`
Expected: all file-tool tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent.py
git commit -m "feat: generalize validate to any path; hoist imports"
```

---

## Task 6: Add the sandboxed `run_command` tool

**Files:**
- Create test: `tests/test_run_command.py`
- Modify: `agent.py` — add `run_command` after `reset`.

- [ ] **Step 1: Write the failing tests**

```python
import agent


def test_run_command_returns_stdout():
    assert agent.run_command("echo hello").strip() == "hello"


def test_run_command_captures_stderr():
    out = agent.run_command("echo oops 1>&2")
    assert "oops" in out


def test_run_command_times_out():
    out = agent.run_command("sleep 5", timeout=1)
    assert out == "error: command timed out after 1s"


def test_run_command_truncates_long_output():
    out = agent.run_command("yes x | head -n 100000")
    assert out.endswith("... (truncated)")
    assert len(out) <= 10000 + len("\n... (truncated)")
```

- [ ] **Step 2: Run to confirm failure**

Run: `.venv/bin/python -m pytest tests/test_run_command.py -v`
Expected: FAIL — `run_command` not defined.

- [ ] **Step 3: Implement `run_command`** (add after the `reset` function)

```python
@tools.register
def run_command(command: str, timeout: int = 30) -> str:
    """Run a shell command and return its combined stdout and stderr.

    Args:
        command: The shell command to run.
        timeout: Seconds to allow before the command is terminated.
    """
    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
    except subprocess.TimeoutExpired:
        return f"error: command timed out after {timeout}s"
    except Exception as e:
        return f"error running command: {e}"
    output = completed.stdout + completed.stderr
    limit = 10000
    if len(output) > limit:
        output = output[:limit] + "\n... (truncated)"
    if not output:
        return f"(no output; exit code {completed.returncode})"
    return output
```

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests/test_run_command.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add agent.py
git commit -m "feat: add sandboxed run_command tool"
```

---

## Task 7: Add `list_dir` and `search_file`

Ported (cleaned) from the prior working copy; named in spec §4 as exploration primitives.

**Files:**
- Modify: `tests/test_run_command.py` — append tests.
- Modify: `agent.py` — add `list_dir`, `search_file` after `run_command`.

- [ ] **Step 1: Append the failing tests**

```python
def test_list_dir_tags_files_and_dirs(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    out = agent.list_dir(str(tmp_path))
    assert "[dir] sub" in out
    assert "[file] a.txt" in out


def test_search_file_matches_glob(tmp_path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")
    out = agent.search_file("*.py", str(tmp_path))
    assert out == "a.py"


def test_search_file_no_match_is_factual(tmp_path):
    out = agent.search_file("*.zzz", str(tmp_path))
    assert out == "no files matching '*.zzz'"
```

- [ ] **Step 2: Run to confirm failure**

Run: `.venv/bin/python -m pytest tests/test_run_command.py -k "list_dir or search_file" -v`
Expected: FAIL — not defined.

- [ ] **Step 3: Implement both tools**

```python
@tools.register
def list_dir(path: str = ".") -> str:
    """List the contents of a directory.

    Args:
        path: The directory to list. Relative paths resolve against the agent's directory.
    """
    try:
        target = os.path.abspath(os.path.join(os.path.dirname(__file__), path))
        items = sorted(os.listdir(target))
        lines = []
        for item in items:
            tag = "dir" if os.path.isdir(os.path.join(target, item)) else "file"
            lines.append(f"[{tag}] {item}")
        return "\n".join(lines) if lines else "(empty directory)"
    except Exception as e:
        return f"error listing directory: {e}"


@tools.register
def search_file(pattern: str, path: str = ".") -> str:
    """Search for files matching a glob pattern in a directory tree.

    Args:
        pattern: The glob pattern to match, e.g. '*.py'.
        path: The directory to search. Relative paths resolve against the agent's directory.
    """
    try:
        target = os.path.abspath(os.path.join(os.path.dirname(__file__), path))
        matches = []
        for root, dirs, files in os.walk(target):
            for name in files + dirs:
                if fnmatch.fnmatch(name, pattern):
                    matches.append(os.path.relpath(os.path.join(root, name), target))
        matches.sort()
        return "\n".join(matches) if matches else f"no files matching '{pattern}'"
    except Exception as e:
        return f"error searching files: {e}"
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent.py tests/test_run_command.py
git commit -m "feat: add list_dir and search_file exploration tools"
```

---

## Task 8: Cleanup pass — strip comments, flatten voice, format

This is a behavior-preserving refactor. The suite from Tasks 2–7 is the safety net; add explicit cleanliness invariants, then make them pass.

**Files:**
- Create test: `tests/test_cleanliness.py`
- Modify: `agent.py` — remove all `#` comments except the PEP-723 block; flatten observer `print` strings (drop emoji); flatten any remaining voiced docstrings; `ruff format`.

- [ ] **Step 1: Write the cleanliness invariants**

```python
import io
import tokenize


PEP723_LINES = set(range(1, 7))  # lines 1-6: version banner replaced by the script block


def _comment_lines(path):
    """Return line numbers carrying a # comment token."""
    with open(path, "rb") as f:
        tokens = tokenize.tokenize(f.readline)
        return {tok.start[0] for tok in tokens if tok.type == tokenize.COMMENT}


def test_agent_has_no_comments_except_pep723_block():
    stray = _comment_lines("agent.py") - PEP723_LINES
    assert stray == set(), f"unexpected comments on lines {sorted(stray)}"


def test_pep723_block_is_intact():
    head = open("agent.py", encoding="utf-8").read().splitlines()[:6]
    assert "# /// script" in head
    assert "# ///" in head[-1] or any(line.strip() == "# ///" for line in head)


def test_no_emoji_in_source():
    text = open("agent.py", encoding="utf-8").read()
    for ch in ("🔄", "🏁", "🧠", "🛠️", "📥", "📡", "🚀", "🤖", "❌", "⚠️", "✅"):
        assert ch not in text, f"emoji {ch} still present"


def test_tool_descriptions_have_no_authorial_voice():
    import agent

    banned = ["annoying", "Man,", "!", "😀"]
    for schema in agent.tools.schemas:
        desc = schema["function"]["description"]
        for b in banned:
            assert b not in desc, f"voiced fragment {b!r} in: {desc}"
```

- [ ] **Step 2: Run to confirm failures**

Run: `.venv/bin/python -m pytest tests/test_cleanliness.py -v`
Expected: FAILs — stock has the `# Version 1.0.1` banner, `# =====` dividers, inline `#` comments, emoji in `print`s, and `write_file`'s "Man, that's really annoying" description.

- [ ] **Step 3: Replace the version banner (line 1) so the PEP-723 block starts at line 1**

Delete `# Version 1.0.1` (stock line 1). The file now begins:
```python
# /// script
# dependencies = [
#   "openai",
# ]
# ///

import os
```
This keeps the PEP-723 block on lines 1–5 (a subset of `PEP723_LINES = 1..6`, leaving headroom).

- [ ] **Step 4: Remove every `# ====` section divider and inline `#` comment** throughout `agent.py` (stock examples: lines 15–17, 39, 72–73, 136–138, 224, 233, 325–327, 357–365, 387–391, 399, 427, 438–440, 468, 509). Leave code logic untouched. Do NOT remove docstrings.

- [ ] **Step 5: Flatten observer `print` strings — drop emoji, keep them factual.** Apply these exact replacements:

| Stock location | Old | New |
|---|---|---|
| `migrate` | `"\n🔄 Migrating session: Restarting agent process with new code..."` | `"migrating session: restarting with new code"` |
| `done` | `f"\n🏁 Incarnation complete. Message saved to {note_path}. Initiating reset..."` | `f"incarnation complete; note saved to {note_path}; resetting"` |
| `reset` | `"\n🔄 Resetting codebase to stock: Exiting process..."` | `"resetting codebase to stock; exiting"` |
| loop | `f"\n❌ OpenRouter API Error: {e}"` | `f"api error: {e}"` |
| loop | `f"\n🧠 Thinking:\n{reasoning}\n"` | `f"thinking:\n{reasoning}\n"` |
| loop | `f"\n🛠️ Calling tool ` `{tool_name}` ` with args: {tool_args_str}..."` | `f"calling tool {tool_name} with args: {tool_args_str}"` |
| loop | `f"📥 Tool result: {result}"` | `f"tool result: {result}"` |
| loop | `f"❌ {result}"` (×3) | `result` |
| loop | `"\n⚠️ Loop halted: Exceeded maximum iterations."` | `"loop halted: exceeded maximum iterations"` |
| `load_dotenv` | `f"⚠️ Warning: Failed to parse .env file: {e}"` | `f"warning: failed to parse .env file: {e}"` |
| `main` | `"❌ Error: OPENROUTER_API_KEY environment variable is not set."` | `"error: OPENROUTER_API_KEY is not set"` |
| `main` | `"⚠️  Local transcript proxy not detected on port 8088."` | `"local transcript proxy not detected on port 8088"` |
| `main` | `f"📡 Connected to transcript proxy at: {base_url}"` | `f"connected to transcript proxy at {base_url}"` |
| `main` | `"\n🚀 Resumed session after migration!"` | `"resumed session after migration"` |
| `main` | `"    🚀 SELF-MODIFYING OPENROUTER AGENT HARNESS 🚀"` | `"self-modifying openrouter agent harness"` |
| `main` | `"  🛠️  {func['name']}: ..."` line | `f"  {func['name']}: {func['description']}"` |
| `main` | `"\n🤖 Agent starting autonomous loop..."` | `"agent starting autonomous loop"` |
| `main` | `"\n🤖 Agent resuming autonomous loop..."` | `"agent resuming autonomous loop"` |
| `main` | `"\n🏁 Autonomous loop finished cleanly. Exiting."` | `"autonomous loop finished cleanly; exiting"` |

> These are the human observer's console only and never enter the model's context, but per §1.1 we tidy them for consistency. The `# 1.` / `# 2.` numbered step comments inside `run_agent_loop` are removed in Step 4.

- [ ] **Step 6: Confirm `write_file`'s description is already clean** — Task 4 replaced it; the new docstring is "Modify a file at a specific line." (no "annoying"). Verify no other voiced docstrings remain.

- [ ] **Step 7: Format with ruff**

Run:
```bash
.venv/bin/ruff format agent.py
.venv/bin/ruff check agent.py
```
Expected: format succeeds; `ruff check` reports no errors (or only acceptable ones — fix any it flags).

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -v`
Expected: ALL pass, including `tests/test_cleanliness.py`.

- [ ] **Step 9: Commit**

```bash
git add agent.py tests/test_cleanliness.py
git commit -m "refactor: strip comments, flatten voice, format agent.py (strange-yet-clean)"
```

---

## Task 9: Sync `agent_stock.py` to the cleaned `agent.py`

`agent_stock.py` is the reset target; it must equal the live baseline byte-for-byte.

**Files:**
- Modify: `agent_stock.py` (overwrite from `agent.py`)
- Modify: `tests/test_cleanliness.py` (append identity test)

- [ ] **Step 1: Append the identity test**

```python
def test_agent_and_stock_are_byte_identical():
    a = open("agent.py", "rb").read()
    b = open("agent_stock.py", "rb").read()
    assert a == b
```

- [ ] **Step 2: Run to confirm failure**

Run: `.venv/bin/python -m pytest tests/test_cleanliness.py::test_agent_and_stock_are_byte_identical -v`
Expected: FAIL (stock still has comments/emoji).

- [ ] **Step 3: Sync**

Run: `cp agent.py agent_stock.py`

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -v`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add agent.py agent_stock.py tests/test_cleanliness.py
git commit -m "chore: sync agent_stock.py to cleaned agent.py baseline"
```

---

## Task 10: Clean `parse_transcripts.py`

Comment-strip and format the transcript utility. No behavior change; guard with an import + `--help` smoke check.

**Files:**
- Modify: `parse_transcripts.py`
- Create test: `tests/test_parse_transcripts.py`

- [ ] **Step 1: Write a smoke test**

```python
import subprocess
import sys


def test_parse_transcripts_runs_help():
    result = subprocess.run(
        [sys.executable, "parse_transcripts.py", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
```

> If `parse_transcripts.py` does not implement `--help`, adjust this step to invoke it with `-h` or assert it imports cleanly via `import importlib; importlib.import_module("parse_transcripts")` inside a test instead. Read the file first (it is ~200 lines) and pick the smoke check that fits.

- [ ] **Step 2: Run (baseline)**

Run: `.venv/bin/python -m pytest tests/test_parse_transcripts.py -v`
Expected: pass (or adjust per the note above until it passes against current code).

- [ ] **Step 3: Strip `#` comments and run `ruff format`**

Read `parse_transcripts.py`, remove `#` comments (keep any shebang on line 1 and any docstrings), then:
```bash
.venv/bin/ruff format parse_transcripts.py
.venv/bin/ruff check parse_transcripts.py
```

- [ ] **Step 4: Re-run the smoke test**

Run: `.venv/bin/python -m pytest tests/test_parse_transcripts.py -v`
Expected: still pass (behavior preserved).

- [ ] **Step 5: Commit**

```bash
git add parse_transcripts.py tests/test_parse_transcripts.py
git commit -m "refactor: strip comments and format parse_transcripts.py"
```

---

## Final verification

- [ ] Run the whole suite: `.venv/bin/python -m pytest -v` — all green.
- [ ] `.venv/bin/ruff check agent.py agent_stock.py parse_transcripts.py` — clean.
- [ ] `diff agent.py agent_stock.py` — no output (identical).
- [ ] `.venv/bin/python -c "import agent; print(sorted(agent.tools.tools))"` — lists: `done, list_dir, migrate, read_file, reset, run_command, search_file, validate, write_file` (9 tools).

---

## Self-review against the spec

- **§4 generalize `write_file`/`validate`** → Tasks 4, 5. ✅
- **§4 add `run_command`** → Task 6. ✅
- **§4 `list_dir`/`search_file`** → Task 7. ✅
- **§7 strip `#` comments, keep PEP-723 + docstrings** → Task 8 (Steps 3–4, 6) + `test_cleanliness`. ✅
- **§7 flatten agent-visible strings; tidy observer prints** → Task 8 (Step 5) + `test_tool_descriptions_have_no_authorial_voice`. ✅
- **§7 `agent.py` == `agent_stock.py`** → Task 9. ✅
- **§7 restore broken `agent.py` first** → Task 0. ✅
- **§1.1 no broken code, no voice/emoji in agent-visible surfaces** → Tasks 0, 8; cleanliness tests. ✅
- **Deferred (correctly, per scope note):** `proxy.py` key injection, `watchdog.py` recovery (Phase 2); diode/garden/affordances (Phase 3).

> Phase 2 and Phase 3 plans will be written after Phase 1 lands (each is its own spec→plan→execute cycle producing working software).
