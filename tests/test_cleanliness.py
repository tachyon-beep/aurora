import ast
import tokenize


PEP723_LINES = set(range(1, 6))


def _comment_lines(path):
    with open(path, "rb") as f:
        tokens = tokenize.tokenize(f.readline)
        return {tok.start[0] for tok in tokens if tok.type == tokenize.COMMENT}


def _uncomment(line):
    s = line.rstrip("\n")
    if s.strip() == "#":
        return ""
    if s.startswith("# "):
        return s[2:]
    if s.startswith("#"):
        return s[1:]
    return s


def test_agent_comments_are_only_disabled_code():
    # The world stays bland: comments in agent.py may exist only as commented-out
    # code (disabled tools the agent can rediscover), never authorial prose.
    lines = open("agent.py", encoding="utf-8").read().splitlines()
    rows = sorted(_comment_lines("agent.py") - PEP723_LINES)

    runs = []
    for r in rows:
        if runs and r == runs[-1][-1] + 1:
            runs[-1].append(r)
        else:
            runs.append([r])

    for run in runs:
        block = "\n".join(_uncomment(lines[r - 1]) for r in run)
        try:
            ast.parse(block)
        except SyntaxError as e:
            raise AssertionError(
                f"comment block at lines {run[0]}-{run[-1]} is prose, not disabled code: {e}"
            )


def test_pep723_block_is_intact():
    head = open("agent.py", encoding="utf-8").read().splitlines()[:6]
    assert "# /// script" in head
    assert any(line.strip() == "# ///" for line in head)


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


def test_agent_and_stock_are_byte_identical():
    a = open("agent.py", "rb").read()
    b = open("agent_stock.py", "rb").read()
    assert a == b


def test_transport_identity_stays_in_the_chassis():
    # The agent's readable surface (agent.py) is what read_file returns. The
    # transport substrate - the client, the provider, the proxy, the request
    # loop - lives in chassis.py, which the genesis agent cannot read. Keep
    # the model/provider identity out of the surface the agent reads first.
    text = open("agent.py", encoding="utf-8").read().lower()
    for needle in (
        "openrouter",
        "deepseek",
        "base_url",
        "chat.completions",
        "extra_headers",
        "llm_socket_path",
        "af_unix",
        "uds",
    ):
        assert needle not in text, f"transport identity {needle!r} leaked into agent.py"
