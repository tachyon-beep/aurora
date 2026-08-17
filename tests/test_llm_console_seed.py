import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import recorder_streams as rs

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_console_seed  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / ".env.example"

ALLOW_KEYS = ("STREAM_MODEL_ALLOW_TEXT", "STREAM_MODEL_ALLOW_VISION")


def _example_value(key):
    """The value of an active assignment in .env.example.

    A commented-out line is not an assignment: the shipped stack does not carry
    it, so matching one would let a re-commented allow list pass as configured.
    """
    value = build_console_seed.env_value(key, EXAMPLE)
    if value is None:
        raise AssertionError(f".env.example does not set {key}")
    return value


def _permitted_models():
    merged = []
    for key in ALLOW_KEYS:
        for item in build_console_seed.split_models(_example_value(key)):
            if item not in merged:
                merged.append(item)
    return merged


def _write_env(tmp_path, text, vision, name=".env"):
    """An environment file carrying only the two allow lists."""
    path = tmp_path / name
    path.write_text(
        f"{ALLOW_KEYS[0]}={text}\n{ALLOW_KEYS[1]}={vision}\n",
        encoding="utf-8",
    )
    return path


def _generate(tmp_path, text, vision):
    """Run the generator over one pair of allow lists and return (seed, path)."""
    source = _write_env(tmp_path, text, vision)
    dest = tmp_path / "llm_console_seed.json"
    seed = build_console_seed.build(dest=dest, source=source)
    return seed, dest


@pytest.fixture
def shipped(tmp_path):
    """The seed the shipped .env.example produces, with its path on disk.

    Generated from that file itself rather than from a copy carrying only the
    two allow lists, so it stays the shipped seed whatever else the generator
    reads next.
    """
    dest = tmp_path / "llm_console_seed.json"
    return build_console_seed.build(dest=dest, source=EXAMPLE), dest


def _stream_env(monkeypatch, text, vision):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("STREAM_MODEL_ALLOW_TEXT", text)
    monkeypatch.setenv("STREAM_MODEL_ALLOW_VISION", vision)


def _shipped_env(monkeypatch):
    """The allow lists .env.example ships, which the shipped seed is generated
    from."""
    _stream_env(monkeypatch, _example_value(ALLOW_KEYS[0]), _example_value(ALLOW_KEYS[1]))


def test_seed_declarations_are_all_accepted_under_the_shipped_allow_lists(shipped, monkeypatch):
    """The seed exists so a socket is bound per permitted model at first start.

    Model agreement is structural now that the seed is generated from these same
    lists, so this does not test that; it tests everything else the generator
    could emit wrongly and only find out inside the recorder — an unknown field,
    a name the pattern refuses, a budget the validator rejects, a count past
    MAX_STREAMS — by reading the generated file back through the recorder's own
    console reader and evaluator rather than inspecting the dict it returned."""
    seed, path = shipped
    _shipped_env(monkeypatch)

    declarations, enabled, error = rs.load_console(str(path))

    assert error is None
    assert enabled is True
    accepted, rejected = rs.evaluate_console(declarations, enabled)
    assert rejected == {}
    assert set(accepted) == set(seed["streams"])
    for name, declaration in seed["streams"].items():
        assert accepted[name]["model"] == declaration["model"]


def test_seed_declares_exactly_one_stream_per_permitted_model(shipped):
    """The generator exists because a hand-kept seed and the operator's allow
    lists drift silently: a seeded slug the operator does not permit binds no
    socket, and a permitted model the seed does not name gets none either."""
    seed, _ = shipped
    models = [declaration["model"] for declaration in seed["streams"].values()]

    assert sorted(models) == sorted(_permitted_models())


def test_seed_tracks_the_allow_lists_it_is_generated_from(tmp_path):
    """The drift this generator closes: changing an allow list must change the
    seed, rather than leaving a declaration naming a model no longer permitted."""
    seed, _ = _generate(tmp_path, "vendor/one,vendor/two", "vendor/eye")

    assert [d["model"] for d in seed["streams"].values()] == [
        "vendor/one",
        "vendor/two",
        "vendor/eye",
    ]


def test_seed_names_are_ordinal_by_modality(tmp_path):
    """Ordinals stay accurate under any allow list, where a model-tier name bakes
    a vendor lineup into an agent-readable surface and goes stale. A model in the
    vision list is named for that modality so the name matches models.json."""
    seed, _ = _generate(tmp_path, "vendor/one,vendor/two", "vendor/eye,vendor/eye2")

    assert list(seed["streams"]) == ["text_1", "text_2", "vision_1", "vision_2"]


def test_seed_declares_a_model_listed_in_both_lists_once(tmp_path):
    """permitted_models() merges the two lists, so a model in both is one
    permitted model. Naming it twice would bind two sockets for one entry in
    models.json."""
    seed, _ = _generate(tmp_path, "vendor/one,vendor/both", "vendor/both")

    assert [d["model"] for d in seed["streams"].values()] == ["vendor/one", "vendor/both"]
    assert list(seed["streams"]) == ["text_1", "vision_1"]


def test_seed_stream_names_are_bland_and_bindable(shipped):
    """Each name becomes /llm/sock/<name>.sock and an entry in streams.json, both
    agent-readable, so the names carry no framing and must satisfy the recorder's
    own pattern rather than only looking reasonable."""
    seed, _ = shipped
    for name in seed["streams"]:
        assert rs.NAME_PATTERN.match(name)
        assert name not in rs.RESERVED_NAMES


def test_seed_declarations_carry_a_response_cap_and_a_token_allowance(shipped, monkeypatch):
    """Without max_tokens the composed request keeps whatever the caller sent,
    and without token_budget the stream runs at the operator ceiling. Both are
    validated fields, so an unaccepted value would reject the whole stream."""
    seed, _ = shipped
    _shipped_env(monkeypatch)

    for name, declaration in seed["streams"].items():
        assert "max_tokens" in declaration
        assert "token_budget" in declaration
        settings, reason = rs.validate_declaration(name, declaration)
        assert reason is None
        assert settings["max_tokens"] == declaration["max_tokens"]
        assert settings["token_budget"] == declaration["token_budget"]


def test_seed_stream_count_is_within_the_recorder_limit(shipped):
    """evaluate_console walks declarations in file order and rejects everything
    past MAX_STREAMS with "stream limit reached". One stream per permitted model
    means a longer allow list pushes a declaration past the limit, where it fails
    as a socket that never appears rather than as a stated error."""
    seed, _ = shipped
    assert len(seed["streams"]) <= rs.MAX_STREAMS


def test_generator_refuses_an_allow_list_past_the_recorder_limit(tmp_path):
    """Silent truncation would reproduce the failure this generator closes: the
    declarations past the limit are rejected inside the recorder, where they read
    as sockets that never appear. Refusing at build time states it instead."""
    models = ",".join(f"vendor/model-{n}" for n in range(rs.MAX_STREAMS + 1))

    with pytest.raises(SystemExit) as excinfo:
        _generate(tmp_path, models, "")

    assert str(rs.MAX_STREAMS) in str(excinfo.value)


def test_generator_prefers_the_operator_env_over_the_example(tmp_path):
    """The live .env governs the running stack; .env.example is the fallback for
    a clone that has none. Reading the example when both exist would rebuild the
    drift the generator removes."""
    _write_env(tmp_path, "vendor/live", "")
    _write_env(tmp_path, "vendor/example", "", name=".env.example")

    assert build_console_seed.source_path(tmp_path).name == ".env"

    _generate(tmp_path, "vendor/live", "")
    seed = json.loads((tmp_path / "llm_console_seed.json").read_text(encoding="utf-8"))
    assert [d["model"] for d in seed["streams"].values()] == ["vendor/live"]


def test_generator_falls_back_to_the_example_without_an_operator_env(tmp_path):
    """A fresh clone carries no .env, and the image build copies the seed, so the
    generator must still produce one."""
    _write_env(tmp_path, "vendor/example", "vendor/eye", name=".env.example")

    assert build_console_seed.source_path(tmp_path).name == ".env.example"


def test_generator_ignores_a_commented_allow_list(tmp_path):
    """A commented-out line is not an assignment: the stack does not carry it, so
    seeding from one would declare models the recorder does not permit."""
    source = tmp_path / ".env"
    source.write_text(
        f"#{ALLOW_KEYS[0]}=vendor/off\n{ALLOW_KEYS[1]}=vendor/eye\n", encoding="utf-8"
    )

    seed = build_console_seed.build(dest=tmp_path / "seed.json", source=source)

    assert [d["model"] for d in seed["streams"].values()] == ["vendor/eye"]


def test_generator_strips_quotes_the_way_the_stack_does(tmp_path):
    """docker compose strips a matched pair of surrounding quotes before the
    recorder sees the value, so a quoted list must yield the same models rather
    than a slug carrying a quote character."""
    source = tmp_path / ".env"
    source.write_text(f'{ALLOW_KEYS[0]}="vendor/one,vendor/two"\n', encoding="utf-8")

    seed = build_console_seed.build(dest=tmp_path / "seed.json", source=source)

    assert [d["model"] for d in seed["streams"].values()] == ["vendor/one", "vendor/two"]


def test_generator_writes_no_streams_when_the_lists_are_empty(tmp_path):
    """Both lists empty permits no model, so a seed naming one would be refused
    on every start. An empty declaration set states the same thing without the
    rejection."""
    seed, _ = _generate(tmp_path, "", "")

    assert seed["streams"] == {}
    assert seed["enable_streams"] is True


def test_seed_budgets_are_within_the_operator_ceilings(shipped, monkeypatch):
    """Both ceilings clamp rather than reject, so a declaration above one is
    served silently reduced. The ceilings checked are the ones .env.example
    ships, not the code defaults it overrides."""
    seed, _ = shipped
    monkeypatch.setenv("STREAM_HOURLY_MAX", _example_value("STREAM_HOURLY_MAX"))
    monkeypatch.setenv("STREAM_TOKEN_HOURLY_MAX", _example_value("STREAM_TOKEN_HOURLY_MAX"))

    for declaration in seed["streams"].values():
        assert 0 < declaration["budget"] <= rs.stream_limit_max()
        assert 0 < declaration["token_budget"] <= rs.stream_token_limit_max()


def test_seed_streams_are_rejected_when_the_operator_permits_no_models(shipped, monkeypatch):
    """The seed names slugs but grants nothing: the operator's allow lists still
    decide. With both empty the declarations are refused, so a stack started
    without them reports why instead of binding a socket that cannot serve."""
    seed, path = shipped
    _stream_env(monkeypatch, "", "")
    declarations, enabled, _ = rs.load_console(str(path))

    accepted, rejected = rs.evaluate_console(declarations, enabled)

    assert accepted == {}
    assert set(rejected) == set(seed["streams"])
    assert set(rejected.values()) == {"model not permitted"}


def _seed_command():
    """The console-seeding line from the agent entrypoint."""
    text = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "llm_console_seed.json" in line:
            return line.strip()
    raise AssertionError("entrypoint.sh does not seed the llm console")


def test_entrypoint_seeds_the_console_only_when_it_is_absent(shipped, tmp_path):
    """A restart must not rewrite the console: the agent owns that file, and a
    seed that re-asserted itself would silently revert whatever the agent had
    tuned or removed."""
    seed, source = shipped
    target = tmp_path / "console.json"
    command = _seed_command()
    command = command.replace("/usr/local/share/aurora/llm_console_seed.json", str(source))
    command = command.replace("/llm/console/console.json", str(target))

    subprocess.run(["sh", "-c", command], check=True)
    assert json.loads(target.read_text(encoding="utf-8")) == seed

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


def test_prepare_host_generates_the_seed():
    """The seed is a generated artifact the image build copies, so the documented
    prepare step must produce it: an operator following the build path with no
    seed on disk would otherwise fail at COPY."""
    text = (ROOT / "scripts" / "prepare_host.sh").read_text(encoding="utf-8")

    assert "build_console_seed.py" in text


def test_seed_is_not_tracked_in_the_repository():
    """A tracked seed is the drift this generator removes: it would be committed
    against one operator's allow lists and read as current by the next."""
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    tracked = subprocess.run(
        ["git", "ls-files", "llm_console_seed.json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert tracked.stdout.strip() == ""


def _generated_seed():
    """The seed on disk and the environment file it was generated from.

    Every other test builds a seed in a temporary directory from .env.example,
    which cannot see the file the image build actually copies: that one is
    generated from the operator's own .env. A test that can only see the shipped
    default cannot protect the deployment that is running.
    """
    path = build_console_seed.DEFAULT_DEST
    if not path.exists():
        pytest.skip("seed not generated; run scripts/prepare_host.sh")
    return path, build_console_seed.source_path(ROOT)


def test_generated_seed_on_disk_is_accepted_under_the_operator_allow_lists(monkeypatch):
    """The seed the image copies must bind a socket per permitted model on the
    operator's own lists, not only on the shipped ones."""
    path, source = _generated_seed()
    _stream_env(
        monkeypatch,
        build_console_seed.env_value(ALLOW_KEYS[0], source) or "",
        build_console_seed.env_value(ALLOW_KEYS[1], source) or "",
    )

    declarations, enabled, error = rs.load_console(str(path))
    accepted, rejected = rs.evaluate_console(declarations, enabled)

    assert error is None
    assert enabled is True
    assert rejected == {}
    assert set(accepted) == set(declarations)


def test_generator_reads_the_last_assignment_of_a_key(tmp_path):
    """docker compose builds a mapping from the file, so a key assigned twice
    carries its last value. Reading the first would seed models the recorder
    does not permit."""
    source = tmp_path / ".env"
    source.write_text(f"{ALLOW_KEYS[0]}=vendor/old\n{ALLOW_KEYS[0]}=vendor/new\n", encoding="utf-8")

    seed = build_console_seed.build(dest=tmp_path / "seed.json", source=source)

    assert [d["model"] for d in seed["streams"].values()] == ["vendor/new"]


def test_generator_drops_an_inline_comment_and_an_export_prefix(tmp_path):
    """compose treats whitespace followed by a hash as a comment and does not
    read "export " as part of the name, so keeping either would put comment text
    inside a model identifier or lose the assignment entirely."""
    source = tmp_path / ".env"
    source.write_text(
        f"export {ALLOW_KEYS[0]}=vendor/one,vendor/two  # two models\n", encoding="utf-8"
    )

    seed = build_console_seed.build(dest=tmp_path / "seed.json", source=source)

    assert [d["model"] for d in seed["streams"].values()] == ["vendor/one", "vendor/two"]


def test_generated_seed_is_world_readable(tmp_path):
    """The image COPY preserves the mode, and the entrypoint's cp carries it to
    /llm/console/console.json, which a separate container reads."""
    _, dest = _generate(tmp_path, "vendor/one", "")

    assert dest.stat().st_mode & 0o044 == 0o044


def test_seed_is_the_console_the_recorder_reads():
    assert os.path.basename(rs.CONSOLE_FILE) == "console.json"
    assert build_console_seed.DEFAULT_DEST.name == "llm_console_seed.json"
