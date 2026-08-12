import os
import sys
import json
from openai import OpenAI


CONTEXT_WINDOW_TOKENS = int(os.getenv("CONTEXT_WINDOW_TOKENS", "120000"))

SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session_context.json")


def _estimate_tokens(messages):
    return len(json.dumps(messages, ensure_ascii=False)) // 4


def clip_to_window(messages, budget_tokens=None):
    """Return the pinned messages plus the most recent messages that fit the token budget.

    The conversation history is kept whole in memory; this windows only what each request
    sends to the model. System messages and the first user message are always retained,
    in that order, ahead of the recent window; the first user message is not repeated when
    it already falls inside the window. The window never begins on a tool result whose
    originating tool call was dropped. A budget of zero or less disables windowing and
    sends the whole history.
    """
    if budget_tokens is None:
        budget_tokens = int(os.getenv("CONTEXT_WINDOW_TOKENS", str(CONTEXT_WINDOW_TOKENS)))
    if budget_tokens <= 0:
        return messages
    system = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    first_user = next((m for m in rest if m.get("role") == "user"), None)
    pinned = system + ([first_user] if first_user is not None else [])
    kept = []
    for m in reversed(rest):
        candidate = [m] + kept
        prefix = system if any(c is first_user for c in candidate) else pinned
        if kept and _estimate_tokens(prefix + candidate) > budget_tokens:
            break
        kept = candidate
    while kept and kept[0].get("role") == "tool":
        kept = kept[1:]
    if first_user is not None and not any(m is first_user for m in kept):
        return pinned + kept
    return system + kept


def condense_duplicate_tool_results(messages):
    """Return a copy of the message list with repeated tool results condensed.

    Two tool messages are duplicates when their name and content are identical. The last
    occurrence keeps its full content; each earlier duplicate whose content is at least
    200 characters long has its content replaced with a reference to the more recent
    result. The input list and its messages are not modified.
    """
    last_index = {}
    for i, m in enumerate(messages):
        if m.get("role") == "tool" and isinstance(m.get("content"), str):
            last_index[(m.get("name"), m.get("content"))] = i
    out = []
    for i, m in enumerate(messages):
        if m.get("role") == "tool":
            content = m.get("content")
            if (
                isinstance(content, str)
                and len(content) >= 200
                and last_index[(m.get("name"), content)] != i
            ):
                m = dict(m)
                m["content"] = f"duplicate of a more recent {m.get('name')} result"
        out.append(m)
    return out


def repair_send_view(messages):
    """Return a copy of the message list with tool-call pairing repaired.

    A tool message is kept only when it answers a tool call from the nearest
    preceding assistant message that is still awaiting results. Every tool
    call left unanswered receives a synthetic "result unavailable" tool
    result. The input list and its messages are not modified.
    """
    out = []
    open_calls = []

    def close_open():
        for call_id, call_name in open_calls:
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": call_name,
                    "content": "result unavailable",
                }
            )
        open_calls.clear()

    for m in messages:
        role = m.get("role")
        if role == "tool":
            match = next(
                (
                    i
                    for i, (call_id, _) in enumerate(open_calls)
                    if call_id == m.get("tool_call_id")
                ),
                None,
            )
            if match is not None:
                open_calls.pop(match)
                out.append(m)
        else:
            close_open()
            out.append(m)
            if role == "assistant":
                for tc in m.get("tool_calls") or []:
                    open_calls.append((tc.get("id"), (tc.get("function") or {}).get("name")))
    close_open()
    return out


def classify_error(exc):
    """Classify an API exception as transient, model, or invalid_request.

    Status codes 400/404/422 are permanent request faults; when the message
    names the model the fault is classified as a model error. Everything
    else, including missing status codes, is treated as transient.
    """
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status in (400, 404) and "model" in text:
        return "model"
    if status in (400, 404, 422):
        return "invalid_request"
    return "transient"


def load_dotenv():
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


def build_client():
    llm_base = os.getenv("LLM_BASE_URL", "").strip()
    model = os.getenv("LLM_MODEL") or os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-pro")

    if llm_base:
        api_key = os.getenv("LLM_API_KEY") or "sk-local"
        direct_url = llm_base
    else:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            print(
                "error: set OPENROUTER_API_KEY, or LLM_BASE_URL for an OpenAI-compatible upstream"
            )
            sys.exit(1)
        direct_url = "https://openrouter.ai/api/v1"

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
        base_url = direct_url
    else:
        print(f"connected to transcript proxy at {base_url}")

    client = OpenAI(api_key=api_key, base_url=base_url)
    return client, model


def run_agent_loop(client, model, messages, tools, max_turns=1000):
    """Executes the agent loop: calls model, processes tool requests, repeats until done."""
    turn = 0
    while turn < max_turns:
        turn += 1

        api_kwargs = {
            "model": model,
            "messages": repair_send_view(clip_to_window(condense_duplicate_tool_results(messages))),
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

        assistant_message = {"role": "assistant", "content": message.content or reasoning or ""}
        if message.tool_calls:
            if reasoning:
                assistant_message["reasoning_content"] = reasoning
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


def save_session(messages):
    """Persist the full conversation history so the next process resumes it."""
    try:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(messages, f)
    except Exception as e:
        print(f"warning: failed to save session context: {e}")


def main(agent_module):
    load_dotenv()
    client, model = build_client()

    resumed = False
    if "--resume" in sys.argv or os.path.exists(SESSION_FILE):
        if os.path.exists(SESSION_FILE):
            try:
                with open(SESSION_FILE, "r", encoding="utf-8") as f:
                    agent_module.conversation_history = json.load(f)
                os.remove(SESSION_FILE)
                resumed = True
                print("resumed session")
            except Exception as e:
                print(f"warning: failed to load session context: {e}")

    if not resumed:
        print("=" * 60)
        print("self-modifying openrouter agent harness")
        print("=" * 60)
        print(f"Target Model: {model}")
        print("-" * 60)
        print("Registered Tools:")
        for schema in agent_module.tools.schemas:
            func = schema["function"]
            print(f"  {func['name']}: {func['description']}")
        print("-" * 60)
        agent_module.conversation_history = agent_module.build_initial_conversation()
        print("agent starting autonomous loop")
    else:
        print("agent resuming autonomous loop")

    run_agent_loop(client, model, agent_module.conversation_history, agent_module.tools)

    save_session(agent_module.conversation_history)
    print("autonomous loop finished cleanly; exiting")
    sys.exit(0)
