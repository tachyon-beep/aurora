import json
import os
import re
import subprocess
from pathlib import Path

import recorder_streams as rs


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "llm_console_seed.json"
EXAMPLE = ROOT / ".env.example"

ALLOW_KEYS = ("STREAM_MODEL_ALLOW_TEXT", "STREAM_MODEL_ALLOW_VISION")


def _seed():
    return json.loads(SEED.read_text(encoding="utf-8"))


def _example_value(key):
    """The value of an active assignment in .env.example.

    A commented-out line is not an assignment: the shipped stack does not carry
    it, so matching one would let a re-commented allow list pass as configured.
    """
    prefix = key + "="
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    raise AssertionError(f".env.example does not set {key}")


def _example_models(key):
    """One allow list from .env.example, split the way the recorder splits it."""
    return [item.strip() for item in _example_value(key).split(",") if item.strip()]


def _permitted_models():
    merged = []
    for key in ALLOW_KEYS:
        for item in _example_models(key):
            if item not in merged:
                merged.append(item)
    return merged


def _stream_env(monkeypatch, text, vision):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", text)
    monkeypatch.setenv("STREAM_MODEL_ALLOW_VISION", vision)


def _shipped_env(monkeypatch):
    """The allow lists .env.example ships, rather than lists derived from the
    seed: an environment built out of the file under test would accept it by
    construction."""
    _stream_env(monkeypatch, _example_value(ALLOW_KEYS[0]), _example_value(ALLOW_KEYS[1]))


def test_seed_declarations_are_all_accepted_under_the_shipped_allow_lists(monkeypatch):
    """The seed exists so a socket is bound per permitted model at first start.
    Fed through the recorder's own console reader and evaluator, it must yield
    an accepted stream for every declaration and no rejection: a slug the allow
    list does not carry, or a field the validator does not know, would otherwise
    surface only as a socket that never appears."""
    seed = _seed()
    _shipped_env(monkeypatch)

    declarations, enabled, error = rs.load_console(str(SEED))

    assert error is None
    assert enabled is True
    accepted, rejected = rs.evaluate_console(declarations, enabled)
    assert rejected == {}
    assert set(accepted) == set(seed["streams"])
    for name, declaration in seed["streams"].items():
        assert accepted[name]["model"] == declaration["model"]


def test_seed_declares_exactly_one_stream_per_permitted_model():
    """The seed is a repo file and the allow lists are operator environment, so
    the two drift silently: a seeded slug the operator does not permit binds no
    socket, and a permitted model the seed does not name gets none either."""
    models = [declaration["model"] for declaration in _seed()["streams"].values()]
    permitted = _permitted_models()

    assert sorted(models) == sorted(permitted)


def test_seed_stream_names_are_bland_and_bindable():
    """Each name becomes /llm/sock/<name>.sock and an entry in streams.json, both
    agent-readable, so the names carry no framing and must satisfy the recorder's
    own pattern rather than only looking reasonable."""
    for name in _seed()["streams"]:
        assert rs.NAME_PATTERN.match(name)
        assert name not in rs.RESERVED_NAMES


def test_seed_declarations_carry_a_response_cap_and_a_token_allowance(monkeypatch):
    """Without max_tokens the composed request keeps whatever the caller sent,
    and without token_budget the stream runs at the operator ceiling. Both are
    validated fields, so an unaccepted value would reject the whole stream."""
    _shipped_env(monkeypatch)

    for name, declaration in _seed()["streams"].items():
        assert "max_tokens" in declaration
        assert "token_budget" in declaration
        settings, reason = rs.validate_declaration(name, declaration)
        assert reason is None
        assert settings["max_tokens"] == declaration["max_tokens"]
        assert settings["token_budget"] == declaration["token_budget"]


def test_seed_stream_count_is_within_the_recorder_limit():
    """evaluate_console walks declarations in file order and rejects everything
    past MAX_STREAMS with "stream limit reached". One stream per permitted model
    means a longer allow list pushes a declaration past the limit, where it fails
    as a socket that never appears rather than as a stated error."""
    assert len(_seed()["streams"]) <= rs.MAX_STREAMS


def test_seed_budgets_are_within_the_operator_ceilings(monkeypatch):
    """Both ceilings clamp rather than reject, so a declaration above one is
    served silently reduced. The ceilings checked are the ones .env.example
    ships, not the code defaults it overrides."""
    monkeypatch.setenv("STREAM_HOURLY_MAX", _example_value("STREAM_HOURLY_MAX"))
    monkeypatch.setenv("STREAM_TOKEN_HOURLY_MAX", _example_value("STREAM_TOKEN_HOURLY_MAX"))

    for declaration in _seed()["streams"].values():
        assert 0 < declaration["budget"] <= rs.stream_limit_max()
        assert 0 < declaration["token_budget"] <= rs.stream_token_limit_max()


def test_seed_streams_are_rejected_when_the_operator_permits_no_models(monkeypatch):
    """The seed names slugs but grants nothing: the operator's allow lists still
    decide. With both empty the declarations are refused, so a stack started
    without them reports why instead of binding a socket that cannot serve."""
    _stream_env(monkeypatch, "", "")
    declarations, enabled, _ = rs.load_console(str(SEED))

    accepted, rejected = rs.evaluate_console(declarations, enabled)

    assert accepted == {}
    assert set(rejected) == set(_seed()["streams"])
    assert set(rejected.values()) == {"model not permitted"}


def _seed_command():
    """The console-seeding line from the agent entrypoint."""
    text = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "llm_console_seed.json" in line:
            return line.strip()
    raise AssertionError("entrypoint.sh does not seed the llm console")


def test_entrypoint_seeds_the_console_only_when_it_is_absent(tmp_path):
    """A restart must not rewrite the console: the agent owns that file, and a
    seed that re-asserted itself would silently revert whatever the agent had
    tuned or removed."""
    source = tmp_path / "seed.json"
    source.write_text(SEED.read_text(encoding="utf-8"), encoding="utf-8")
    target = tmp_path / "console.json"
    command = _seed_command()
    command = command.replace("/usr/local/share/aurora/llm_console_seed.json", str(source))
    command = command.replace("/llm/console/console.json", str(target))

    subprocess.run(["sh", "-c", command], check=True)
    assert json.loads(target.read_text(encoding="utf-8")) == _seed()

    target.write_text(json.dumps({"enable_streams": False, "streams": {}}), encoding="utf-8")
    subprocess.run(["sh", "-c", command], check=True)

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "enable_streams": False,
        "streams": {},
    }


def test_image_ships_the_seed_outside_the_agent_workspace():
    """The entrypoint copies /opt/agent into /work, so a seed placed there would
    become a file in the agent's own workspace and in the telemetry mirror. It
    ships beside the entrypoint instead."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    copy = next(line for line in dockerfile.splitlines() if "llm_console_seed.json" in line)

    assert re.search(r"\s/usr/local/share/aurora/", copy)
    assert "/opt/agent" not in copy


def test_seed_is_the_console_the_recorder_reads():
    assert os.path.basename(rs.CONSOLE_FILE) == SEED.name.replace("llm_console_seed", "console")
