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
