import os
import sys
import json
from openai import OpenAI


CONTEXT_WINDOW_TOKENS = int(os.getenv("CONTEXT_WINDOW_TOKENS", "120000"))


def _estimate_tokens(messages):
    return len(json.dumps(messages, ensure_ascii=False)) // 4


def clip_to_window(messages, budget_tokens=None):
    """Return the system messages plus the most recent messages that fit the token budget.

    The conversation history is kept whole in memory; this windows only what each request
    sends to the model. System messages are always retained, and the window never begins on
    a tool result whose originating tool call was dropped. A budget of zero or less disables
    windowing and sends the whole history.
    """
    if budget_tokens is None:
        budget_tokens = CONTEXT_WINDOW_TOKENS
    if budget_tokens <= 0:
        return messages
    system = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    kept = []
    for m in reversed(rest):
        candidate = [m] + kept
        if kept and _estimate_tokens(system + candidate) > budget_tokens:
            break
        kept = candidate
    while kept and kept[0].get("role") == "tool":
        kept = kept[1:]
    return system + kept


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

    client = OpenAI(api_key=api_key, base_url=base_url)
    return client, model


def run_agent_loop(client, model, messages, tools, max_turns=1000):
    """Executes the agent loop: calls model, processes tool requests, repeats until done."""
    turn = 0
    while turn < max_turns:
        turn += 1

        api_kwargs = {
            "model": model,
            "messages": clip_to_window(messages),
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

        assistant_message = {"role": "assistant"}
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


def main(agent_module):
    load_dotenv()
    client, model = build_client()

    session_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session_context.json")

    resumed = False
    if "--resume" in sys.argv or os.path.exists(session_file):
        if os.path.exists(session_file):
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    agent_module.conversation_history = json.load(f)
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
        for schema in agent_module.tools.schemas:
            func = schema["function"]
            print(f"  {func['name']}: {func['description']}")
        print("-" * 60)
        agent_module.conversation_history = agent_module.build_initial_conversation()
        print("agent starting autonomous loop")
    else:
        print("agent resuming autonomous loop")

    run_agent_loop(client, model, agent_module.conversation_history, agent_module.tools)

    print("autonomous loop finished cleanly; exiting")
    sys.exit(0)
