def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_compose_defines_stage_service_and_telemetry_volume():
    text = _read("docker-compose.yml")
    assert "telemetry:/telemetry:ro" in text
    assert "telemetry:/telemetry\n" in text or "- telemetry:/telemetry\n" in text
    assert '"127.0.0.1:8091:8091"' in text
    assert '"127.0.0.1:8092:8092"' in text
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


def test_the_socket_volumes_are_declared():
    text = _read("docker-compose.yml")
    assert "  llm_sock: {}" in text
    assert "  llm_console: {}" in text


def test_the_agent_image_precreates_the_socket_mountpoints():
    # Docker copies image-mountpoint ownership into each newly created empty
    # volume, so both paths must appear on the chown as well as the mkdir. A
    # mkdir-only mountpoint arrives root-owned and the recorder cannot bind.
    text = _read("Dockerfile")
    chown_line = next(line for line in text.splitlines() if "chown appuser:appuser /" in line)
    for path in ("/llm/sock", "/llm/console"):
        assert path in text
        assert path in chown_line
