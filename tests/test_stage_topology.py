def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_compose_defines_stage_service_and_telemetry_volume():
    text = _read("docker-compose.yml")
    assert "telemetry:/telemetry:ro" in text
    assert "telemetry:/telemetry\n" in text or "- telemetry:/telemetry\n" in text
    assert '"127.0.0.1:${STAGE_STREAM_PORT:-8091}:8091"' in text
    assert '"127.0.0.1:${STAGE_CONSOLE_PORT:-8092}:8092"' in text
    assert "STAGE_CONSOLE_TOKEN" in text
    assert "aurora-stage" in text
    assert "cloudflared" in text
    stage_block = text.split("stage:")[1].split("cloudflared:")[0]
    assert "state:/state" not in stage_block


def test_stage_and_cloudflared_are_isolated_from_egress():
    text = _read("docker-compose.yml")
    stage_block = text.split("\n  stage:\n")[1].split("\n  cloudflared:\n")[0]
    cloudflared_block = text.split("\n  cloudflared:\n")[1].split("\nnetworks:\n")[0]
    assert "networks: [stream]" in stage_block
    assert "egress" not in stage_block
    assert "networks: [stream]" in cloudflared_block
    assert "egress" not in cloudflared_block
    assert "stream: {}" in text


def test_agent_image_precreates_telemetry_mountpoint():
    text = _read("Dockerfile")
    assert "/telemetry" in text


def test_stage_dockerfile_copies_only_stage_package():
    text = _read("Dockerfile.stage")
    assert "COPY" in text and "stage" in text
    assert "agent.py" not in text


def test_env_example_documents_stage_settings():
    text = _read(".env.example")
    assert "STAGE_CONSOLE_TOKEN" in text
    assert "TUNNEL_TOKEN" in text
    compose = _read("docker-compose.yml")
    for name in (
        "STAGE_SUMMARY_API_KEY",
        "STAGE_SUMMARY_BASE_URL",
        "STAGE_SUMMARY_MODEL",
        "STAGE_SUMMARY_INTERVAL_SECONDS",
    ):
        assert name in text
        assert name in compose


def test_verify_script_checks_stage_containment():
    text = _read("scripts/verify_container.sh")
    assert "8091" in text and "8092" in text
    assert "aurora-stage" in text


def test_speech_credential_is_declared_only_on_the_diode_service():
    compose = _read("docker-compose.yml")
    diode_block = compose.split("\n  diode:\n")[1].split("\n  viewer:\n")[0]
    assert "ELEVENLABS_API_KEY: ${ELEVENLABS_API_KEY:-}" in diode_block
    agent_block = compose.split("\n  agent:\n")[1].split("\n  diode:\n")[0]
    assert "ELEVENLABS" not in agent_block
    assert _read(".env.example").count("ELEVENLABS_API_KEY") >= 1


def test_speech_credential_is_absent_from_the_agent_image():
    assert "ELEVENLABS" not in _read("Dockerfile")


def test_agent_work_allocation_and_memory_move_together():
    text = _read("docker-compose.yml")
    agent_block = text.split("\n  agent:\n")[1].split("\n  diode:\n")[0]
    assert "/work:size=4g,uid=1000,gid=1000" in agent_block
    assert "mem_limit: 5g" in agent_block


def _agent_block(text):
    return text.split("\n  agent:\n")[1].split("\n  diode:\n")[0]


def _recorder_block(text):
    return text.split("\n  recorder:\n")[1].split("\n  agent:\n")[0]


def test_compose_no_longer_defines_an_internal_network():
    text = _read("docker-compose.yml")
    assert "internal: true" not in text
    assert "networks: [internal]" not in text
    assert "networks: [internal, egress]" not in text


def test_the_agent_has_no_network_interface():
    agent = _agent_block(_read("docker-compose.yml"))
    assert "network_mode: none" in agent
    assert "networks:" not in agent


def test_the_agent_resolves_its_own_hostname_without_dns():
    agent = _agent_block(_read("docker-compose.yml"))
    assert "hostname: agent" in agent
    assert '"agent:127.0.0.1"' in agent
    assert "dns:" in agent
    assert "- 127.0.0.1" in agent


def test_the_agent_mounts_the_socket_directory_read_only():
    text = _read("docker-compose.yml")
    assert "llm_sock:/llm/sock:ro" in _agent_block(text)
    assert "llm_sock:/llm/sock\n" in _recorder_block(text)


def test_the_console_volume_is_writable_by_the_agent_only():
    text = _read("docker-compose.yml")
    assert "llm_console:/llm/console\n" in _agent_block(text)
    assert "llm_console:/llm/console:ro" in _recorder_block(text)


def test_the_recorder_receives_the_operator_stream_ceiling():
    recorder = _recorder_block(_read("docker-compose.yml"))
    assert "STREAM_HOURLY_MAX: ${STREAM_HOURLY_MAX:-}" in recorder


def test_the_recorder_receives_the_operator_model_allow_lists():
    text = _read("docker-compose.yml")
    recorder = _recorder_block(text)
    assert "STREAM_MODEL_ALLOW_TEXT: ${STREAM_MODEL_ALLOW_TEXT:-}" in recorder
    assert "STREAM_MODEL_ALLOW_VISION: ${STREAM_MODEL_ALLOW_VISION:-}" in recorder
    assert "STREAM_UPSTREAM_URL: ${STREAM_UPSTREAM_URL:-}" in recorder
    agent = _agent_block(text)
    assert "STREAM_MODEL_ALLOW" not in agent
    assert "STREAM_UPSTREAM_URL" not in agent


def test_the_diode_receives_the_operator_budget_ceiling():
    text = _read("docker-compose.yml")
    diode_block = text.split("\n  diode:\n")[1].split("\n  sense:\n")[0]
    assert "DIODE_HOURLY_MAX: ${DIODE_HOURLY_MAX:-}" in diode_block
    agent = _agent_block(text)
    assert "DIODE_HOURLY_MAX" not in agent


def test_the_socket_volumes_are_declared():
    text = _read("docker-compose.yml")
    assert "  llm_sock: {}" in text
    assert "  llm_console: {}" in text


def _sense_block(text):
    return text.split("\n  sense:\n")[1].split("\n  viewer:\n")[0]


def test_the_agent_mounts_sense_read_only():
    text = _read("docker-compose.yml")
    assert "./volumes/sense:/sense:ro" in _agent_block(text)
    assert "/sense" in _read("Dockerfile")


def test_sense_mounts_only_its_own_volume():
    sense = _sense_block(_read("docker-compose.yml"))
    volume_lines = [
        line.strip()
        for line in sense.split("volumes:\n")[1].splitlines()
        if line.strip().startswith("- ")
    ]
    mounts = []
    for line in volume_lines:
        if line == "- /tmp":
            break
        mounts.append(line)
    assert mounts == ["- ./volumes/sense:/sense"]
    for name in ("diode", "state", "llm", "transcripts", "telemetry"):
        assert f"{name}:" not in sense.split("volumes:\n")[1]


def test_sense_environment_holds_no_credential():
    sense = _sense_block(_read("docker-compose.yml"))
    for marker in ("_KEY", "_TOKEN", "_SECRET"):
        assert marker not in sense


def test_sense_capture_tools_stay_out_of_the_agent_manifest():
    assert "yt-dlp" not in _read("requirements-agent.txt")


def test_sense_shares_no_network_with_the_viewer():
    # Without an explicit networks key, sense would join the implicit compose
    # default network alongside the viewer; the viewer must stay its default
    # network's sole occupant.
    text = _read("docker-compose.yml")
    sense = _sense_block(text)
    assert "networks: [sense_egress]" in sense
    assert "network_mode" not in sense
    assert "sense_egress: {}" in text
    viewer = text.split("\n  viewer:\n")[1].split("\n  stage:\n")[0]
    assert "networks" not in viewer
    assert "sense_egress" not in viewer
    # The only mentions of the sense network are its declaration and the
    # sense service's own attachment: no other service joins it.
    assert text.count("sense_egress") == 2


def test_sense_runs_under_an_init_process():
    assert "init: true" in _sense_block(_read("docker-compose.yml"))


def test_verify_script_covers_sense_volume_network_and_liveness():
    text = _read("scripts/verify_container.sh")
    assert "create_sense_volume.sh" in text
    assert "status.json" in text
    assert "_default" in text


def test_the_agent_image_precreates_the_socket_mountpoints():
    # Docker copies image-mountpoint ownership into each newly created empty
    # volume, so both paths must appear on the chown as well as the mkdir. A
    # mkdir-only mountpoint arrives root-owned and the recorder cannot bind.
    text = _read("Dockerfile")
    chown_line = next(line for line in text.splitlines() if "chown appuser:appuser /" in line)
    for path in ("/llm/sock", "/llm/console"):
        assert path in text
        assert path in chown_line
