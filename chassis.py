import datetime
import os
import shutil
import sys
import json
import time
import httpx
from openai import OpenAI


CONTEXT_WINDOW_TOKENS = int(os.getenv("CONTEXT_WINDOW_TOKENS", "120000"))

REASONING_EFFORT_LEVELS = ("none", "low", "medium", "high")
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "")

LLM_SOCKET_PATH = os.getenv("LLM_SOCKET_PATH", "/llm/sock/core.sock")
SOCKET_WAIT_SECONDS = 30

SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session_context.json")

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
EXIT_TERMINATED = 43
EXIT_ENVIRONMENT = 44


def reasoning_effort():
    """Return the configured reasoning effort, or None when unset or unrecognised.

    Recognised values are listed in REASONING_EFFORT_LEVELS. When this returns
    None the request omits the field and the model applies its own default.
    """
    value = os.getenv("REASONING_EFFORT", REASONING_EFFORT).strip().lower()
    return value if value in REASONING_EFFORT_LEVELS else None


def _estimate_tokens(messages):
    return len(json.dumps(messages, ensure_ascii=False)) // 4


def _message_length(message):
    """The serialised length of one message, in the units _estimate_tokens counts."""
    return len(json.dumps(message, ensure_ascii=False))


def _window_total(count, length_sum):
    """The estimate for a message list of count members whose lengths sum to length_sum.

    json.dumps writes a list as its members joined by ", " inside brackets, so
    the whole document's length follows from the members' lengths. The window
    below grows one message at a time; deriving the total this way keeps each
    step from re-serialising every message already in it.
    """
    if count <= 0:
        return 0
    return (2 + length_sum + 2 * (count - 1)) // 4


def clip_to_window(messages, budget_tokens=None, eviction_chunk_tokens=None):
    """Return the pinned messages plus the most recent messages that fit the token budget.

    The conversation history is kept whole in memory; this windows only what each request
    sends to the model. System messages and the first user message are always retained,
    in that order, ahead of the recent window; the first user message is not repeated when
    it already falls inside the window. The window never begins on a tool result whose
    originating tool call was dropped. A budget of zero or less disables windowing and
    sends the whole history.

    Eviction advances in chunks: once any messages must be dropped, the dropped prefix is
    extended to the next multiple of eviction_chunk_tokens, counted from the start of the
    history, so the window start holds one position while the history grows and
    consecutive requests share a stable prefix. The chunk defaults to
    CONTEXT_WINDOW_EVICTION_TOKENS, or to an eighth of the budget when that is unset; a
    chunk of zero or less evicts per message.
    """
    if budget_tokens is None:
        budget_tokens = int(os.getenv("CONTEXT_WINDOW_TOKENS", str(CONTEXT_WINDOW_TOKENS)))
    if budget_tokens <= 0:
        return messages
    if eviction_chunk_tokens is None:
        configured = os.getenv("CONTEXT_WINDOW_EVICTION_TOKENS", "").strip()
        eviction_chunk_tokens = int(configured) if configured else budget_tokens // 8
    system = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    first_user = next((m for m in rest if m.get("role") == "user"), None)
    pinned = system + ([first_user] if first_user is not None else [])
    system_lengths = [_message_length(m) for m in system]
    system_total = (len(system_lengths), sum(system_lengths))
    pinned_total = system_total
    if first_user is not None:
        pinned_total = (system_total[0] + 1, system_total[1] + _message_length(first_user))
    newest_first = []
    kept_count = 0
    kept_sum = 0
    holds_first_user = False
    for m in reversed(rest):
        length = _message_length(m)
        holds = holds_first_user or m is first_user
        prefix_count, prefix_sum = system_total if holds else pinned_total
        count = prefix_count + 1 + kept_count
        total = prefix_sum + length + kept_sum
        if newest_first and _window_total(count, total) > budget_tokens:
            break
        newest_first.append(m)
        kept_count += 1
        kept_sum += length
        holds_first_user = holds
    kept = newest_first[::-1]
    start = len(rest) - len(kept)
    chunk_chars = 4 * eviction_chunk_tokens
    if start > 0 and chunk_chars > 0:
        dropped = sum(_message_length(m) for m in rest[:start])
        boundary = -(-dropped // chunk_chars) * chunk_chars
        while start < len(rest) - 1 and dropped < boundary:
            dropped += _message_length(rest[start])
            start += 1
        kept = rest[start:]
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


EXHAUSTION_PHRASES = (
    "insufficient credit",
    "insufficient_quota",
    "quota exceeded",
    "exceeded your current quota",
)


def classify_error(exc):
    """Classify an API exception as transient, model, or invalid_request.

    A 400 or 404 whose message names the model by identity (phrases such as
    "not a valid model", "model not found", "no endpoints found", or both
    "model" and "does not exist") is a model error. A spent credit balance is
    transient whatever status carries it, by 402 or by an EXHAUSTION_PHRASES
    message: the request is well formed and repairing it changes nothing.
    Any other status from 400 up to (but not including) 500, except 402, 408
    and 429, is a permanent request fault. 402, 408, 429, 5xx, and missing
    status codes are transient.
    """
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status in (400, 404) and (
        "not a valid model" in text
        or "model not found" in text
        or "no endpoints found" in text
        or ("model" in text and "does not exist" in text)
    ):
        return "model"
    if any(phrase in text for phrase in EXHAUSTION_PHRASES):
        return "transient"
    if status is not None and 400 <= status < 500 and status not in (402, 408, 429):
        return "invalid_request"
    return "transient"


TRANSIENT_RETRIES = 5
BACKOFF_SECONDS = [1, 2, 4, 8, 16]


class UnrecoverableRequestError(Exception):
    """An unrepairable request fault; the incarnation must end."""


class EnvironmentFailure(Exception):
    """The upstream stayed unreachable through bounded retries."""


def default_model():
    """The model named by the environment, matching build_client's selection."""
    return os.getenv("LLM_MODEL") or "deepseek/deepseek-v4-pro"


def strip_reasoning(messages):
    """Return a copy of the message list without reasoning_content fields."""
    out = []
    for m in messages:
        if "reasoning_content" in m:
            m = {k: v for k, v in m.items() if k != "reasoning_content"}
        out.append(m)
    return out


def create_with_recovery(client, api_kwargs, full_history, sleep=time.sleep):
    """Send one completion request, recovering from classified failures.

    Transient failures retry with backoff and raise EnvironmentFailure when
    retries are exhausted. A model error retries once with the
    environment-default model; the swap persists in api_kwargs. Any other
    invalid request retries once with an aggressively repaired send view.
    Faults that survive their retry raise UnrecoverableRequestError.
    """
    transient = 0
    tried_model_swap = False
    tried_deep_repair = False
    while True:
        try:
            return client.chat.completions.create(**api_kwargs)
        except Exception as e:
            kind = classify_error(e)
            if kind == "transient":
                if transient >= TRANSIENT_RETRIES:
                    raise EnvironmentFailure(str(e))
                sleep(BACKOFF_SECONDS[min(transient, len(BACKOFF_SECONDS) - 1)])
                transient += 1
            elif kind == "model":
                if tried_model_swap or api_kwargs.get("model") == default_model():
                    raise UnrecoverableRequestError(f"model rejected upstream: {e}")
                api_kwargs["model"] = default_model()
                tried_model_swap = True
            else:
                if tried_deep_repair:
                    raise UnrecoverableRequestError(f"request rejected upstream after repair: {e}")
                api_kwargs["messages"] = repair_send_view(
                    strip_reasoning(clip_to_window(condense_duplicate_tool_results(full_history)))
                )
                tried_deep_repair = True


def archive_corrupt_session(session_file=None, work_dir=None):
    """Move an unreadable session file into tombstones/ and note the loss."""
    if session_file is None:
        session_file = SESSION_FILE
    if work_dir is None:
        work_dir = WORK_DIR
    if not os.path.exists(session_file):
        return
    tombstone_dir = os.path.join(work_dir, "tombstones")
    os.makedirs(tombstone_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = os.path.join(tombstone_dir, f"corrupt_session_{stamp}.json")
    try:
        shutil.move(session_file, dest)
    except OSError:
        return
    note = (
        "the saved session could not be read and was moved to "
        f"tombstones/corrupt_session_{stamp}.json. this incarnation starts "
        "without it.\n"
    )
    with open(
        os.path.join(tombstone_dir, f"corrupt_session_{stamp}.txt"), "w", encoding="utf-8"
    ) as f:
        f.write(note)
    with open(os.path.join(tombstone_dir, "synthetic_note.txt"), "w", encoding="utf-8") as f:
        f.write(note)


def terminate_incarnation(messages, reason, work_dir=None, session_file=None):
    """Record a harness-terminated incarnation and exit with code 43.

    Writes a synthetic tombstone note, archives the session history beside
    it, and removes the saved session so the fault is not resumed.
    """
    if work_dir is None:
        work_dir = WORK_DIR
    if session_file is None:
        session_file = SESSION_FILE
    tombstone_dir = os.path.join(work_dir, "tombstones")
    os.makedirs(tombstone_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    archive_name = f"session_{stamp}.json"
    try:
        with open(os.path.join(tombstone_dir, archive_name), "w", encoding="utf-8") as f:
            json.dump(messages, f)
    except Exception:
        archive_name = "(archive failed)"
    note = (
        "this incarnation was terminated by the harness.\n"
        f"reason: {reason}\n"
        f"messages in history: {len(messages)}\n"
        f"the session history was archived to tombstones/{archive_name}\n"
    )
    note_path = os.path.join(tombstone_dir, f"incarnation-{stamp}-{os.getpid()}.txt")
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(note)
    with open(os.path.join(tombstone_dir, "synthetic_note.txt"), "w", encoding="utf-8") as f:
        f.write(note)
    try:
        os.remove(session_file)
    except OSError:
        pass
    print(f"harness terminated incarnation: {reason}")
    sys.stdout.flush()
    sys.exit(EXIT_TERMINATED)


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


def socket_path():
    """The configured model socket path, or None when it is explicitly disabled.

    An unset variable takes the default path: the container has no network, so it
    must not silently fall back to one. Only an explicitly empty value selects the
    direct upstream, which is the path used when running outside the containers.
    """
    value = os.getenv("LLM_SOCKET_PATH", LLM_SOCKET_PATH)
    return value.strip() or None


def wait_for_socket(path, timeout=None, sleep=time.sleep):
    """True once path exists; False when timeout elapses first."""
    if timeout is None:
        timeout = SOCKET_WAIT_SECONDS
    waited = 0.0
    while True:
        if os.path.exists(path):
            return True
        if waited >= timeout:
            return False
        sleep(0.5)
        waited += 0.5


def build_client():
    llm_base = os.getenv("LLM_BASE_URL", "").strip()
    model = default_model()

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

    path = socket_path()
    if path is None:
        print(f"no model socket configured; connecting directly to {direct_url}")
        return OpenAI(api_key=api_key, base_url=direct_url), model

    if not wait_for_socket(path):
        raise EnvironmentFailure(
            f"model socket {path} did not appear within {SOCKET_WAIT_SECONDS}s"
        )

    print(f"connected to transcript proxy at {path}")
    client = OpenAI(
        api_key=api_key,
        base_url="http://localhost/api/v1",
        http_client=httpx.Client(transport=httpx.HTTPTransport(uds=path)),
    )
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

        effort = reasoning_effort()
        if effort:
            api_kwargs["reasoning_effort"] = effort

        if tools.schemas:
            api_kwargs["tools"] = tools.schemas
            api_kwargs["tool_choice"] = "auto"

        response = create_with_recovery(client, api_kwargs, messages)
        model = api_kwargs.get("model", model)

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
    try:
        client, model = build_client()
    except EnvironmentFailure as e:
        print(f"environment failure: {e}")
        sys.exit(EXIT_ENVIRONMENT)

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
                archive_corrupt_session()

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

    try:
        run_agent_loop(client, model, agent_module.conversation_history, agent_module.tools)
    except UnrecoverableRequestError as e:
        terminate_incarnation(agent_module.conversation_history, str(e))
    except EnvironmentFailure as e:
        print(f"environment failure: {e}")
        save_session(agent_module.conversation_history)
        sys.exit(EXIT_ENVIRONMENT)

    save_session(agent_module.conversation_history)
    print("autonomous loop finished cleanly; exiting")
    sys.exit(0)
