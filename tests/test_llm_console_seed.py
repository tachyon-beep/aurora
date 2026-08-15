import json
import os
import re
import subprocess
from pathlib import Path

import recorder_streams as rs


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "llm_console_seed.json"


def _seed():
    return json.loads(SEED.read_text(encoding="utf-8"))


def _stream_env(monkeypatch, text, vision):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", text)
    monkeypatch.setenv("STREAM_MODEL_ALLOW_VISION", vision)


def test_seed_declares_one_text_and_one_vision_stream(monkeypatch):
    """The seed exists so two sockets are bound at first start. Fed through the
    recorder's own console reader and evaluator, it must yield two accepted
    streams and no rejection: a slug the allow list does not carry, or a field
    the validator does not know, would otherwise surface only as a socket that
    never appears."""
    seed = _seed()
    models = {name: d["model"] for name, d in seed["streams"].items()}
    _stream_env(monkeypatch, models["text"], models["vision"])

    declarations, enabled, error = rs.load_console(str(SEED))

    assert error is None
    assert enabled is True
    accepted, rejected = rs.evaluate_console(declarations, enabled)
    assert rejected == {}
    assert set(accepted) == {"text", "vision"}
    assert accepted["text"]["model"] == models["text"]
    assert accepted["vision"]["model"] == models["vision"]


def test_seed_stream_names_are_bland_and_bindable():
    """Each name becomes /llm/sock/<name>.sock and an entry in streams.json, both
    agent-readable, so the names carry no framing and must satisfy the recorder's
    own pattern rather than only looking reasonable."""
    for name in _seed()["streams"]:
        assert rs.NAME_PATTERN.match(name)
        assert name not in rs.RESERVED_NAMES


def test_seed_budgets_are_within_the_operator_ceiling(monkeypatch):
    monkeypatch.delenv("STREAM_HOURLY_MAX", raising=False)
    for declaration in _seed()["streams"].values():
        assert 0 < declaration["budget"] <= rs.stream_limit_max()


def test_seed_streams_are_rejected_when_the_operator_permits_no_models(monkeypatch):
    """The seed names slugs but grants nothing: the operator's allow lists still
    decide. With both empty the declarations are refused, so a stack started
    without them reports why instead of binding a socket that cannot serve."""
    _stream_env(monkeypatch, "", "")
    declarations, enabled, _ = rs.load_console(str(SEED))

    accepted, rejected = rs.evaluate_console(declarations, enabled)

    assert accepted == {}
    assert set(rejected) == {"text", "vision"}
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


def test_documented_allow_lists_carry_the_seeded_models():
    """The seed is inert unless the operator's lists carry its slugs, so the
    template that teaches those lists must name the same ones."""
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    seed = _seed()["streams"]

    for key, name in (("STREAM_MODEL_ALLOW_TEXT", "text"), ("STREAM_MODEL_ALLOW_VISION", "vision")):
        line = next(li for li in example.splitlines() if li.lstrip("#").startswith(key + "="))
        assert seed[name]["model"] in line.split("=", 1)[1].split(",")


def test_seed_is_the_console_the_recorder_reads():
    assert os.path.basename(rs.CONSOLE_FILE) == SEED.name.replace("llm_console_seed", "console")
