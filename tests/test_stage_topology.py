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


def test_verify_script_checks_stage_containment():
    text = _read("scripts/verify_container.sh")
    assert "8091" in text and "8092" in text
    assert "aurora-stage" in text
