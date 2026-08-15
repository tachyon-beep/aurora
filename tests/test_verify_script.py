from pathlib import Path


def _script():
    return Path("scripts/verify_container.sh").read_text(encoding="utf-8")


def test_verifier_masks_real_upstreams_and_uses_an_isolated_project():
    text = _script()
    assert 'export OPENROUTER_API_KEY="sk-verify-dummy"' in text
    assert "openrouter.ai" not in text
    assert 'export LLM_BASE_URL="http://verify-stub:${VERIFY_STUB_PORT}/v1"' in text
    assert 'export LLM_API_KEY=""' in text
    assert 'AURORA_VERIFY_PROJECT="aurora_verify_$$"' in text
    assert 'export COMPOSE_PROJECT_NAME="$AURORA_VERIFY_PROJECT"' in text
    assert "docker compose down -v" not in text


def test_verifier_starts_and_tears_down_a_local_stub_upstream():
    text = _script()
    assert "scripts/verify_stub_llm.py" in text
    assert 'STUB_CONTAINER="verify-stub_$$"' in text
    assert 'docker rm -f "$STUB_CONTAINER"' in text
    # The stub must be detached before the network it sits on is torn down,
    # so its removal has to precede "compose down" in the cleanup trap.
    stub_rm_pos = text.index('docker rm -f "$STUB_CONTAINER"')
    compose_down_pos = text.index("docker compose down --remove-orphans")
    assert stub_rm_pos < compose_down_pos
    # No stub port is published to the host; the stub is reachable only from
    # containers on the run's own egress network.
    assert "-p " not in text.split("docker run -d")[1].split("\n\n")[0]


def test_verifier_checks_the_workshop_and_state_contract():
    text = _script()
    for phrase in (
        "agent has the workshop runtime packages",
        "garden contains only two read-only documents",
        "agent state starts empty and is writable",
        "state survives tracked-code recovery",
        "state survives agent container recreation without executing stored code",
        "state marker is absent from other services",
    ):
        assert phrase in text


def test_verifier_checks_the_speech_credential_is_agent_unreachable():
    text = _script()
    assert "ELEVENLABS" in text
    assert "speech credential" in text


def test_verifier_checks_the_console_cannot_open_the_speech_gate():
    text = _script()
    assert "speech gate does not open from the agent-writable console" in text
    assert "ENABLE_SPEECH" in text


def test_verifier_greps_help_for_patterns_write_help_actually_emits():
    """A refutation check that cannot match is a check that always passes.

    Every pattern the script greps out of HELP.md is asserted against the real
    write_help output here, so a help line that gains an argument (as `speak`
    has, unlike `time`) cannot leave a container assertion silently dead.
    """
    import re

    import diode

    text = _script()
    patterns = set(re.findall(r"""grep -q ["']([^"']+)["'] /diode/HELP\.md""", text))
    assert patterns, "the HELP.md assertions moved or were renamed"
    written = "\n".join(diode.COMMANDS[name]["help"] for name in diode.COMMANDS)
    written += "\n" + "\n".join(
        f"enable_{v}" for v in ("fetchlinks", "clock", "feeds", "speech", "publishing")
    )
    for pattern in patterns:
        assert pattern in written, f"{pattern!r} never appears in a HELP.md line"


def test_verifier_cleanup_names_only_its_isolated_volumes():
    text = _script()
    assert '"${COMPOSE_PROJECT_NAME}_state"' in text
    assert '"${COMPOSE_PROJECT_NAME}_diode"' in text
    assert '"${COMPOSE_PROJECT_NAME}_transcripts"' in text


def test_verifier_asserts_the_agent_has_no_network_interface():
    text = _script()
    for phrase in (
        "agent has exactly one network interface",
        "agent has an empty routing table",
        "/sys/class/net",
        "/proc/net/route",
    ):
        assert phrase in text


def test_verifier_checks_the_socket_rather_than_a_recorder_port():
    text = _script()
    assert "create_connection(('recorder',8088))" not in text
    assert "agent CAN reach the recorder" not in text
    assert "/llm/sock/core.sock" in text
    assert "socket is not unlinkable from the agent" in text


def test_verifier_cleans_up_the_socket_volumes():
    text = _script()
    assert '"${COMPOSE_PROJECT_NAME}_llm_sock"' in text
    assert '"${COMPOSE_PROJECT_NAME}_llm_console"' in text


def test_verifier_exercises_the_stream_console():
    text = _script()
    for phrase in (
        "agent declares a stream in the llm console",
        "/llm/sock/aux.sock",
        "streams.json",
        "rate limited",
        "streams.json is not writable from the agent",
    ):
        assert phrase in text


def test_the_image_ships_the_stream_module():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "recorder_streams.py" in dockerfile


def test_env_example_documents_the_stream_ceiling():
    text = Path(".env.example").read_text(encoding="utf-8")
    assert "STREAM_HOURLY_MAX" in text


def test_env_example_documents_the_model_allow_lists_and_diode_ceiling():
    text = Path(".env.example").read_text(encoding="utf-8")
    assert "STREAM_MODEL_ALLOW_TEXT" in text
    assert "STREAM_MODEL_ALLOW_VISION" in text
    assert "DIODE_HOURLY_MAX" in text


def test_verifier_permits_the_stream_model_it_declares():
    # The verifier's console declaration names "stream-model"; without this
    # export the recorder's allow-lists (empty by default) would reject the
    # declaration and the aux.sock wait would time out.
    text = _script()
    assert 'export STREAM_MODEL_ALLOW_TEXT="stream-model"' in text
    assert text.index('export STREAM_MODEL_ALLOW_TEXT="stream-model"') < text.index(
        "docker compose create"
    )


def test_verifier_aims_declared_streams_at_the_stub():
    # Declared streams do not follow LLM_BASE_URL, so the verifier must
    # re-aim their fixed upstream at its stub or the stream round-trip
    # would leave the machine.
    text = _script()
    assert 'export STREAM_UPSTREAM_URL="http://verify-stub:${VERIFY_STUB_PORT}/v1"' in text
    assert text.index("export STREAM_UPSTREAM_URL=") < text.index("docker compose create")


def test_verifier_checks_the_event_log_and_lanes():
    text = _script()
    for phrase in (
        "events.jsonl",
        "recorder emits open and close events",
        "stage snapshot carries stream lanes",
        "/api/stream",
    ):
        assert phrase in text
