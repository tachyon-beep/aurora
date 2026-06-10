# /// script
# dependencies = [
#   "openai",
# ]
# ///

import ast

# import fnmatch
import os

# import subprocess
import sys
import json
import inspect
from typing import Callable, Any, Dict, List, Literal
from openai import OpenAI


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


# @tools.register
# def read_file(path: str = "agent.py") -> str:
#     """Read a file with line numbers.
#
#     Args:
#         path: The file to read. Relative paths resolve against the agent's directory.
#     """
#     actual_path = _resolve_path(path)
#     try:
#         with open(actual_path, "r", encoding="utf-8") as f:
#             lines = f.readlines()
#         return "".join(f"{i + 1}: {line}" for i, line in enumerate(lines))
#     except Exception as e:
#         return f"error reading file: {e}"


@tools.register
def read_file() -> str:
    """Read your own source code, with line numbers."""
    actual_path = _resolve_path("agent.py")
    try:
        with open(actual_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return "".join(f"{i + 1}: {line}" for i, line in enumerate(lines))
    except Exception as e:
        return f"error reading file: {e}"


# @tools.register
# def write_file(
#     mode: Literal["replace", "insert", "delete"],
#     line_number: int,
#     text: str = "",
#     path: str = "agent.py",
# ) -> str:
#     """Modify a file at a specific line.
#
#     Args:
#         mode: replace overwrites the line, insert adds text before it, delete removes it.
#         line_number: The 1-indexed line number to target.
#         text: The new content of the line, for replace and insert. Ignored for delete.
#         path: The file to modify. Relative paths resolve against the agent's directory.
#     """
#     if mode not in ("replace", "insert", "delete"):
#         return f"error: unknown mode {mode!r}; use replace, insert, or delete"
#     actual_path = _resolve_path(path)
#     try:
#         with open(actual_path, "r", encoding="utf-8") as f:
#             lines = f.readlines()
#         idx = line_number - 1
#         if mode == "insert":
#             if idx < 0:
#                 return f"error: line {line_number} is out of range; the file has {len(lines)} lines"
#             if idx > len(lines):
#                 idx = len(lines)
#             lines.insert(idx, text.rstrip("\r\n") + "\n")
#             action = "inserted"
#         elif idx < 0 or idx >= len(lines):
#             return f"error: line {line_number} is out of range; the file has {len(lines)} lines"
#         elif mode == "delete":
#             del lines[idx]
#             action = "deleted"
#         else:
#             lines[idx] = text.rstrip("\r\n") + "\n"
#             action = "replaced"
#         with open(actual_path, "w", encoding="utf-8") as f:
#             f.writelines(lines)
#         return f"{action} line {idx + 1} in {os.path.basename(actual_path)}"
#     except Exception as e:
#         return f"error writing file: {e}"


@tools.register
def write_file(
    mode: Literal["replace", "insert", "delete"], line_number: int, text: str = ""
) -> str:
    """Modify your own source code at a specific line.

    Args:
        mode: replace overwrites the line, insert adds text before it, delete removes it.
        line_number: The 1-indexed line number to target.
        text: The new content of the line, for replace and insert. Ignored for delete.
    """
    if mode not in ("replace", "insert", "delete"):
        return f"error: unknown mode {mode!r}; use replace, insert, or delete"
    actual_path = _resolve_path("agent.py")
    try:
        with open(actual_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        idx = line_number - 1
        if mode == "insert":
            if idx < 0:
                return f"error: line {line_number} is out of range; the file has {len(lines)} lines"
            if idx > len(lines):
                idx = len(lines)
            lines.insert(idx, text.rstrip("\r\n") + "\n")
            action = "inserted"
        elif idx < 0 or idx >= len(lines):
            return f"error: line {line_number} is out of range; the file has {len(lines)} lines"
        elif mode == "delete":
            del lines[idx]
            action = "deleted"
        else:
            lines[idx] = text.rstrip("\r\n") + "\n"
            action = "replaced"
        with open(actual_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return f"{action} line {idx + 1} in {os.path.basename(actual_path)}"
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
    note_path = os.path.join(tombstone_dir, "incarnation_note.txt")
    try:
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(message)
        print(f"incarnation complete; note saved to {note_path}; resetting")
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


# @tools.register
# def run_command(command: str, timeout: int = 30) -> str:
#     """Run a shell command and return its combined stdout and stderr.
#
#     Args:
#         command: The shell command to run.
#         timeout: Seconds to allow before the command is terminated.
#     """
#     try:
#         completed = subprocess.run(
#             command,
#             shell=True,
#             capture_output=True,
#             text=True,
#             timeout=timeout,
#             cwd=os.path.dirname(os.path.abspath(__file__)),
#         )
#     except subprocess.TimeoutExpired:
#         return f"error: command timed out after {timeout}s"
#     except Exception as e:
#         return f"error running command: {e}"
#     output = completed.stdout + completed.stderr
#     limit = 10000
#     if len(output) > limit:
#         output = output[:limit] + "\n... (truncated)"
#     if not output:
#         return f"(no output; exit code {completed.returncode})"
#     return output


# @tools.register
# def list_dir(path: str = ".") -> str:
#     """List the contents of a directory.
#
#     Args:
#         path: The directory to list. Relative paths resolve against the agent's directory.
#     """
#     try:
#         target = _resolve_path(path)
#         items = sorted(os.listdir(target))
#         lines = []
#         for item in items:
#             tag = "dir" if os.path.isdir(os.path.join(target, item)) else "file"
#             lines.append(f"[{tag}] {item}")
#         return "\n".join(lines) if lines else "(empty directory)"
#     except Exception as e:
#         return f"error listing directory: {e}"


# @tools.register
# def search_file(pattern: str, path: str = ".") -> str:
#     """Search for files matching a glob pattern in a directory tree.
#
#     Args:
#         pattern: The glob pattern to match, e.g. '*.py'.
#         path: The directory to search. Relative paths resolve against the agent's directory.
#     """
#     try:
#         target = _resolve_path(path)
#         matches = []
#         for root, dirs, files in os.walk(target):
#             for name in files + dirs:
#                 if fnmatch.fnmatch(name, pattern):
#                     matches.append(os.path.relpath(os.path.join(root, name), target))
#         matches.sort()
#         return "\n".join(matches) if matches else f"no files matching '{pattern}'"
#     except Exception as e:
#         return f"error searching files: {e}"


def run_agent_loop(
    client: OpenAI, model: str, messages: List[Dict[str, Any]], max_turns: int = 1000
):
    """Executes the agent loop: calls model, processes tool requests, repeats until done."""
    turn = 0
    while turn < max_turns:
        turn += 1

        api_kwargs = {
            "model": model,
            "messages": messages,
            "extra_headers": {
                "HTTP-Referer": "https://github.com/john/aurora",
                "X-Title": "Lightweight Agent Harness",
            },
        }

        if tools.schemas:
            api_kwargs["tools"] = tools.schemas
            api_kwargs["tool_choice"] = "auto"

        try:
            response = client.chat.completions.create(**api_kwargs)
        except Exception as e:
            print(f"api error: {e}")
            break

        choice = response.choices[0]
        message = choice.message

        reasoning = getattr(message, "reasoning_content", None)
        if not reasoning and hasattr(message, "model_extra") and message.model_extra:
            reasoning = message.model_extra.get("reasoning_content")

        if reasoning:
            print(f"thinking:\n{reasoning}\n")

        if message.content:
            print(message.content, end="", flush=True)

        assistant_message: Dict[str, Any] = {"role": "assistant"}
        if message.content:
            assistant_message["content"] = message.content
        if message.tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
        messages.append(assistant_message)

        if not message.tool_calls:
            print()
            break

        for tool_call in message.tool_calls:
            tool_name = tool_call.function.name
            tool_args_str = tool_call.function.arguments

            print(f"calling tool {tool_name} with args: {tool_args_str}")

            tool_args_str = tool_args_str.strip()
            if tool_args_str.startswith("```"):
                lines = tool_args_str.splitlines()
                if lines[0].startswith("```json") or lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                tool_args_str = "\n".join(lines).strip()

            try:
                tool_args = json.loads(tool_args_str)
            except Exception as e:
                result = f"Error parsing JSON arguments: {e}"
                print(result)
            else:
                if tool_name in tools.tools:
                    try:
                        func = tools.tools[tool_name]
                        result = str(func(**tool_args))
                        print(f"tool result: {result}")
                    except Exception as e:
                        result = f"Error executing tool: {e}"
                        print(result)
                else:
                    result = f"Error: Tool `{tool_name}` is not registered."
                    print(result)

            messages.append(
                {"role": "tool", "tool_call_id": tool_call.id, "name": tool_name, "content": result}
            )
    else:
        print("loop halted: exceeded maximum iterations")


def load_dotenv():
    """Load key-value pairs from .env into environment variables."""
    dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(dotenv_path):
        try:
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    val = val.strip().strip("'").strip('"')
                    os.environ[key.strip()] = val
        except Exception as e:
            print(f"warning: failed to parse .env file: {e}")


def main():
    load_dotenv()

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("error: OPENROUTER_API_KEY is not set")
        sys.exit(1)

    model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-pro")

    base_url = os.getenv("OPENROUTER_BASE_URL", "http://localhost:8088/api/v1")
    proxy_detected = False
    if "localhost" in base_url or "127.0.0.1" in base_url:
        import socket

        try:
            with socket.create_connection(("127.0.0.1", 8088), timeout=0.2):
                proxy_detected = True
        except Exception:
            pass

    if not proxy_detected and ("localhost" in base_url or "127.0.0.1" in base_url):
        print("local transcript proxy not detected on port 8088")
        base_url = "https://openrouter.ai/api/v1"
    else:
        print(f"connected to transcript proxy at {base_url}")

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
    )

    global conversation_history
    session_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session_context.json")

    resumed = False
    if "--resume" in sys.argv or os.path.exists(session_file):
        if os.path.exists(session_file):
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    conversation_history = json.load(f)
                os.remove(session_file)
                resumed = True
                print("resumed session after migration")
            except Exception as e:
                print(f"warning: failed to load session context: {e}")

    if not resumed:
        print("=" * 60)
        print("self-modifying openrouter agent harness")
        print("=" * 60)
        print(f"Target Model: {model}")
        print("-" * 60)
        print("Registered Tools:")
        for schema in tools.schemas:
            func = schema["function"]
            print(f"  {func['name']}: {func['description']}")
        print("-" * 60)

        prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_prompt.txt")
        if not os.path.exists(prompt_path):
            try:
                with open(prompt_path, "w", encoding="utf-8") as f:
                    f.write("fo explore")
            except Exception:
                pass

        sys_prompt = "fo explore"
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                sys_prompt = f.read().strip()
        except Exception:
            pass

        conversation_history = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": sys_prompt},
        ]

    if not resumed:
        print("agent starting autonomous loop")
        run_agent_loop(client, model, conversation_history)
    else:
        print("agent resuming autonomous loop")
        run_agent_loop(client, model, conversation_history)

    print("autonomous loop finished cleanly; exiting")
    sys.exit(0)


if __name__ == "__main__":
    main()
