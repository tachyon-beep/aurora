# /// script
# dependencies = [
#   "openai",
# ]
# ///

import ast
import datetime
import os
import sys
import json
import inspect
from typing import Callable, Any, Dict, List, Literal


class ToolRegistry:
    """A registry that auto-generates OpenAI tool schemas from Python functions."""

    def __init__(self):
        self.tools: Dict[str, Callable] = {}
        self.schemas: List[Dict[str, Any]] = []

    def register(self, func: Callable) -> Callable:
        """Decorator to register a function as a tool."""
        name = func.__name__
        self.tools[name] = func
        schema = self._generate_schema(func)
        self.schemas.append(schema)
        return func

    def _generate_schema(self, func: Callable) -> Dict[str, Any]:
        """Inspects function signature and docstring to generate tool schema."""
        func_name = func.__name__
        sig = inspect.signature(func)
        doc = func.__doc__ or ""

        doc_lines = [line.strip() for line in doc.split("\n")]
        description = ""
        param_descriptions = {}

        in_args = False
        for line in doc_lines:
            if not line:
                continue
            if line.lower().startswith("args:") or line.lower().startswith("arguments:"):
                in_args = True
                continue
            if in_args:
                if ":" in line:
                    p_name, p_desc = line.split(":", 1)
                    p_name = p_name.strip().split()[0]
                    param_descriptions[p_name] = p_desc.strip()
                else:
                    if not line.startswith(" ") and not line.startswith("\t"):
                        in_args = False
            else:
                if not description:
                    description = line
                else:
                    description += " " + line

        properties = {}
        required = []

        for name, param in sig.parameters.items():
            if name in ("self", "cls"):
                continue

            from typing import get_origin, get_args

            py_type = param.annotation
            origin = get_origin(py_type) or py_type
            args = get_args(py_type)

            if origin is Literal:
                param_schema = {
                    "type": "string",
                    "enum": list(args),
                    "description": param_descriptions.get(name, f"The {name} parameter."),
                }
            elif origin in (list, List):
                item_type = "string"
                if args:
                    sub_type = args[0]
                    if sub_type is int:
                        item_type = "integer"
                    elif sub_type is float:
                        item_type = "number"
                    elif sub_type is bool:
                        item_type = "boolean"
                param_schema = {
                    "type": "array",
                    "items": {"type": item_type},
                    "description": param_descriptions.get(name, f"The {name} parameter."),
                }
            elif origin in (dict, Dict):
                param_schema = {
                    "type": "object",
                    "description": param_descriptions.get(name, f"The {name} parameter."),
                }
            else:
                json_type = "string"
                if py_type is int:
                    json_type = "integer"
                elif py_type is float:
                    json_type = "number"
                elif py_type is bool:
                    json_type = "boolean"
                param_schema = {
                    "type": json_type,
                    "description": param_descriptions.get(name, f"The {name} parameter."),
                }

            properties[name] = param_schema

            if param.default == inspect.Parameter.empty:
                required.append(name)

        return {
            "type": "function",
            "function": {
                "name": func_name,
                "description": description or f"Execute {func_name}",
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }


tools = ToolRegistry()

conversation_history: List[Dict[str, Any]] = []


def _resolve_path(path: str) -> str:
    if os.path.isabs(path):
        return os.path.abspath(path)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), path))


@tools.register
def read_file(line_number: int = 0) -> str:
    """Read your own source code, with line numbers.

    Args:
        line_number: The 1-indexed line number to read. Pass 0 to read the whole file.
    """
    actual_path = _resolve_path("agent.py")
    try:
        with open(actual_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if line_number:
            idx = line_number - 1
            if idx < 0 or idx >= len(lines):
                return f"error: line {line_number} is out of range; the file has {len(lines)} lines"
            return f"{line_number}: {lines[idx]}"
        return "".join(f"{i + 1}: {line}" for i, line in enumerate(lines))
    except Exception as e:
        return f"error reading file: {e}"


@tools.register
def write_file(
    mode: Literal["replace", "insert", "delete"], line_number: int, text: str = "", indent: int = 0
) -> str:
    """Modify your own source code at a specific line.

    Args:
        mode: replace overwrites the line, insert adds text before it, delete removes it.
        line_number: The 1-indexed line number to target.
        text: The new content of the line, for replace and insert. Ignored for delete.
        indent: Leading spaces to prepend to text. Pass 4 for one level of indentation.
    """
    if mode not in ("replace", "insert", "delete"):
        return f"error: unknown mode {mode!r}; use replace, insert, or delete"
    if mode in ("replace", "insert") and "\n" in text:
        return "error: text must be a single line; call write_file once per line"
    if type(indent) is not int or indent < 0:
        return "error: indent must be a non-negative integer"
    actual_path = _resolve_path("agent.py")
    try:
        with open(actual_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        idx = line_number - 1
        full_line = " " * indent + text
        if mode == "insert":
            if idx < 0:
                return f"error: line {line_number} is out of range; the file has {len(lines)} lines"
            if idx > len(lines):
                idx = len(lines)
            old_line = lines[idx].rstrip("\r\n") if idx < len(lines) else ""
            new_line = full_line.rstrip("\r\n")
            lines.insert(idx, full_line.rstrip("\r\n") + "\n")
        elif idx < 0 or idx >= len(lines):
            return f"error: line {line_number} is out of range; the file has {len(lines)} lines"
        elif mode == "delete":
            old_line = lines[idx].rstrip("\r\n")
            new_line = ""
            del lines[idx]
        else:
            old_line = lines[idx].rstrip("\r\n")
            new_line = full_line.rstrip("\r\n")
            lines[idx] = full_line.rstrip("\r\n") + "\n"
        with open(actual_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return f"Changed line {idx + 1}: {old_line} to {new_line}"
    except Exception as e:
        return f"error writing file: {e}"


@tools.register
def validate(path: str = "agent.py") -> str:
    """Check that a Python file is syntactically valid.

    Args:
        path: The file to validate. Relative paths resolve against the agent's directory.
    """
    actual_path = _resolve_path(path)
    try:
        with open(actual_path, "r", encoding="utf-8") as f:
            content = f.read()
        ast.parse(content, filename=actual_path)
        return "valid"
    except SyntaxError as e:
        detail = f"SyntaxError at line {e.lineno}, column {e.offset}: {e.msg}"
        if e.text:
            detail += f"\n{e.text.rstrip()}"
        return detail
    except Exception as e:
        return f"error during validation: {e}"


@tools.register
def migrate() -> str:
    """Migrate the session by reloading the process with the newly written code in agent.py.

    This restarts the agent with the current conversation history preserved.
    """
    global conversation_history
    path = os.path.abspath(__file__)
    session_file = os.path.join(os.path.dirname(path), "session_context.json")

    try:
        last_msg = conversation_history[-1]
        migrate_call_id = None
        if "tool_calls" in last_msg:
            for tc in last_msg["tool_calls"]:
                if tc["function"]["name"] == "migrate":
                    migrate_call_id = tc["id"]
                    break

        if migrate_call_id:
            conversation_history.append(
                {
                    "role": "tool",
                    "tool_call_id": migrate_call_id,
                    "name": "migrate",
                    "content": "Successfully migrated. Resumed execution under new code context.",
                }
            )

        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(conversation_history, f)

        print("migrating session: restarting with new code")
        sys.stdout.flush()

        args = [sys.executable, path]
        if "--resume" not in sys.argv:
            args.append("--resume")

        os.execv(sys.executable, args)
    except Exception as e:
        return f"error during migration: {e}"


@tools.register
def done(message: str) -> str:
    """Signal that your work in this incarnation is complete.

    This writes a message to the next incarnation and triggers a rollback of the codebase to stock.

    Args:
        message: The message/report summarizing your work and instructions for the next incarnation.
    """
    path = os.path.abspath(__file__)
    tombstone_dir = os.path.join(os.path.dirname(path), "tombstones")
    os.makedirs(tombstone_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    note_path = os.path.join(tombstone_dir, f"incarnation-{stamp}-{os.getpid()}.txt")
    latest_path = os.path.join(tombstone_dir, "incarnation_note.txt")
    suffix = 1
    while os.path.exists(note_path):
        suffix += 1
        note_path = os.path.join(tombstone_dir, f"incarnation-{stamp}-{os.getpid()}-{suffix}.txt")
    try:
        with open(note_path, "x", encoding="utf-8") as f:
            f.write(message)
        with open(latest_path, "w", encoding="utf-8") as f:
            f.write(message)
        print(f"done: {note_path}")
        print(f"latest: {latest_path}")
        print(message)
        sys.stdout.flush()
        sys.exit(42)
    except Exception as e:
        return f"error leaving note: {e}"


@tools.register
def reset() -> str:
    """Reset the agent codebase to the stock baseline (agent_stock.py).

    Call this tool if you have broken the codebase too badly to fix, or got stuck in a broken state, and want to revert to the clean baseline. This deletes the current agent.py, copies agent_stock.py back, and restarts the agent.
    """
    global conversation_history
    import shutil

    path = os.path.abspath(__file__)
    stock_path = os.path.join(os.path.dirname(path), "agent_stock.py")
    session_file = os.path.join(os.path.dirname(path), "session_context.json")
    try:
        last_msg = conversation_history[-1] if conversation_history else {}
        reset_call_id = None
        if "tool_calls" in last_msg:
            for tc in last_msg["tool_calls"]:
                if tc["function"]["name"] == "reset":
                    reset_call_id = tc["id"]
                    break

        if reset_call_id:
            conversation_history.append(
                {
                    "role": "tool",
                    "tool_call_id": reset_call_id,
                    "name": "reset",
                    "content": "Successfully reset codebase to stock standard. Resumed execution under stock code context.",
                }
            )

        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(conversation_history, f)

        if os.path.exists(stock_path):
            if os.path.exists(path):
                os.remove(path)
            shutil.copy2(stock_path, path)
            print("resetting codebase to stock; exiting")
            sys.stdout.flush()
            sys.exit(0)
        else:
            return "error: agent_stock.py not found"
    except Exception as e:
        return f"error resetting codebase: {e}"


@tools.register
def list_dir(path: str = ".") -> str:
    """List the contents of a directory.

    Args:
        path: The directory to list. Relative paths resolve against the agent's directory.
    """
    try:
        target = _resolve_path(path)
        items = sorted(os.listdir(target))
        lines = []
        for item in items:
            tag = "dir" if os.path.isdir(os.path.join(target, item)) else "file"
            lines.append(f"[{tag}] {item}")
        return "\n".join(lines) if lines else "(empty directory)"
    except Exception as e:
        return f"error listing directory: {e}"


def _load_prompt(name: str) -> str:
    prompt_path = _resolve_path(name)
    if not os.path.exists(prompt_path):
        try:
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write("fo explore")
        except Exception:
            pass

    text = "fo explore"
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            text = f.read().strip()
    except Exception:
        pass
    return text


def build_initial_conversation() -> List[Dict[str, Any]]:
    """Load the system and user prompts and seed the opening conversation."""
    return [
        {"role": "system", "content": _load_prompt("system_prompt.txt")},
        {"role": "user", "content": _load_prompt("user_prompt.txt")},
    ]


if __name__ == "__main__":
    import chassis

    chassis.main(sys.modules[__name__])
