from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _service_block(compose: str, name: str, end_marker: str) -> str:
    start_marker = f"  {name}:\n"
    start = compose.index(start_marker)
    end = compose.index(end_marker, start + len(start_marker))
    return compose[start:end]


def test_state_volume_is_private_to_agent() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    agent = _service_block(compose, "agent", "  diode:\n")
    networks_start = compose.index("\nnetworks:\n")
    volumes_start = compose.index("\nvolumes:\n", networks_start)
    volumes = compose[volumes_start + 1 :]

    assert "      - state:/state\n" in agent
    assert "  state: {}\n" in volumes


def test_dockerfile_seeds_state_mountpoint_ownership() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "mkdir -p /diode /transcripts /state" in dockerfile
    assert "chown appuser:appuser /diode /transcripts /state" in dockerfile


def test_state_has_no_automatic_runtime_consumers() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    service_blocks = {
        "recorder": _service_block(compose, "recorder", "  agent:\n"),
        "diode": _service_block(compose, "diode", "  viewer:\n"),
        "viewer": _service_block(compose, "viewer", "\nnetworks:\n"),
    }

    for service, block in service_blocks.items():
        assert "state:/state" not in block, f"{service} must not mount private agent state"

    lifecycle_files = (
        "entrypoint.sh",
        "agent.py",
        "agent_stock.py",
        "chassis.py",
        "watchdog.py",
        "proxy.py",
        "diode.py",
        "viewer.py",
        "pump.py",
        "sense.py",
        "video.py",
    )
    for relative in lifecycle_files:
        contents = (ROOT / relative).read_text()
        assert "/state" not in contents, (
            f"{relative} must not reference /state; automatic coupling would make "
            "the persistent volume non-inert"
        )
