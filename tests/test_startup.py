import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SERVICES = ("recorder", "agent", "diode", "sense", "viewer", "stage", "cloudflared")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _service_block(name: str) -> str:
    compose = _read("docker-compose.yml")
    start = compose.index(f"\n  {name}:\n")
    for following in SERVICES[SERVICES.index(name) + 1 :]:
        marker = f"\n  {following}:\n"
        if marker in compose:
            return compose[start : compose.index(marker, start)]
    return compose[start : compose.index("\nnetworks:\n", start)]


def _pump_command() -> str:
    line = next(li for li in _read("entrypoint.sh").splitlines() if "pump.py" in li)
    return line.strip()


def test_host_preparation_provisions_bind_mounts_before_building_the_garden() -> None:
    script = _read("scripts/prepare_host.sh")
    garden = script.index("scripts/build_garden.py")

    assert script.index("scripts/create_build_volume.sh") < garden
    assert script.index("scripts/create_sense_volume.sh") < garden


def test_host_preparation_builds_missing_offline_assets_before_the_garden() -> None:
    script = _read("scripts/prepare_host.sh")
    vendor = script.index("scripts/build_vendor.sh")
    garden = script.index("scripts/build_garden.py")

    assert vendor < garden
    for asset in (
        "vendor/registry",
        "vendor/lisp/bundle.lisp",
        "vendor/models/potion-base-8m/model.safetensors",
        "vendor/models/potion-base-8m/tokenizer.json",
        "vendor/models/potion-base-8m/config.json",
    ):
        assert asset in script


def test_every_advertised_quick_start_runs_host_preparation_before_compose() -> None:
    readme_quick_start = (
        _read("README.md").split("## Quick start", 1)[1].split("### Building the workshop", 1)[0]
    )
    agents_commands = (
        _read("AGENTS.md")
        .split("## Build, Test, and Development Commands", 1)[1]
        .split("## Coding Style", 1)[0]
    )
    site = _read("site/index.html")

    for text in (readme_quick_start, agents_commands):
        assert "scripts/prepare_host.sh" in text
        assert text.index("scripts/prepare_host.sh") < text.index("docker compose")
    for element_id in ("hero-command", "quickstart-code"):
        command = site.split(f'id="{element_id}"', 1)[1].split("</", 1)[0]
        assert "scripts/prepare_host.sh" in command
        assert command.index("scripts/prepare_host.sh") < command.index("docker compose")


def test_container_verifier_builds_missing_vendor_assets_before_images() -> None:
    script = _read("scripts/verify_container.sh")
    vendor = script.index("scripts/build_vendor.sh")

    assert vendor < script.index("docker build")
    assert vendor < script.index("docker compose build")


def test_entrypoint_starts_the_pump_before_handing_off_to_the_watchdog() -> None:
    script = _read("entrypoint.sh")
    command = _pump_command()

    assert command.endswith("&")
    assert script.index(command) < script.index("exec python watchdog.py")


def test_the_watchdog_is_still_what_the_entrypoint_execs() -> None:
    script = _read("entrypoint.sh")
    lines = [line for line in script.splitlines() if line.strip()]

    assert lines[-1] == "exec python watchdog.py"
    assert len([line for line in lines if line.startswith("exec ")]) == 1


def test_a_crashing_pump_is_restarted_by_the_loop(tmp_path) -> None:
    # entrypoint.sh runs under set -e, which is not suspended inside a loop
    # body: an unguarded non-zero exit ends the restart loop for the life of
    # the container rather than restarting the pump.
    marker = tmp_path / "starts"
    command = _pump_command()
    command = command.replace(
        "python /usr/local/bin/pump.py", f"sh -c 'echo tick >> {marker}; exit 1'"
    )
    command = command.replace("sleep 5", "sleep 0.05")
    script = f"set -eu\n{command}\nsleep 0.6\nkill $! 2>/dev/null || true\n"

    subprocess.run(["sh", "-c", script], check=True, timeout=30)

    assert len(marker.read_text(encoding="utf-8").split()) >= 2


def test_the_image_ships_the_pump_outside_the_agent_workspace() -> None:
    # The entrypoint copies /opt/agent into /work, so a pump placed there would
    # become a file in the agent's own workspace and in the telemetry mirror.
    dockerfile = _read("Dockerfile")
    copy = next(line for line in dockerfile.splitlines() if "pump.py" in line)
    workspace = next(line for line in dockerfile.splitlines() if " /opt/agent/" in line)

    assert "/usr/local/bin/pump.py" in copy
    assert "/opt/agent" not in copy
    assert "pump.py" not in workspace


def test_the_image_precreates_the_pump_mountpoint() -> None:
    # Docker copies image-mountpoint ownership into a newly created empty
    # volume; a mkdir-only mountpoint arrives root-owned and the agent, which
    # runs as uid 1000, cannot write to it.
    dockerfile = _read("Dockerfile")
    mkdir_line = next(line for line in dockerfile.splitlines() if "mkdir -p /diode" in line)
    chown_line = next(line for line in dockerfile.splitlines() if "chown appuser:appuser /" in line)

    assert "/pump" in mkdir_line
    assert "/pump" in chown_line


def test_the_pump_volume_is_mounted_into_the_agent_alone() -> None:
    assert "- pump:/pump\n" in _service_block("agent")
    for name in ("recorder", "diode", "sense", "viewer", "stage", "cloudflared"):
        assert "pump:/pump" not in _service_block(name)
    assert "  pump: {}" in _read("docker-compose.yml")


def test_the_agent_receives_the_pump_resource_limits() -> None:
    agent = _service_block("agent")
    example = _read(".env.example")

    assert "PUMP_MAX_ENTRIES: ${PUMP_MAX_ENTRIES:-32}" in agent
    assert "PUMP_MAX_CONCURRENT: ${PUMP_MAX_CONCURRENT:-8}" in agent
    assert "PUMP_MAX_ENTRIES" in example
    assert "PUMP_MAX_CONCURRENT" in example


def test_the_recorder_bounds_its_connection_fan_out() -> None:
    # Under HTTP/1.1 keep-alive a connection holds a thread for its whole life,
    # and Linux counts threads against the pids cgroup.
    recorder = _service_block("recorder")

    assert "pids_limit: 512" in recorder
    assert "mem_limit: 512m" in recorder


def test_the_recorder_receives_the_operator_token_ceiling() -> None:
    assert "STREAM_TOKEN_HOURLY_MAX: ${STREAM_TOKEN_HOURLY_MAX:-}" in _service_block("recorder")
    assert "STREAM_TOKEN_HOURLY_MAX" not in _service_block("agent")


def test_the_shipped_template_permits_models_out_of_the_box() -> None:
    # Both lists commented out is a stack in which no declaration is permitted
    # and no socket binds.
    lines = _read(".env.example").splitlines()

    assert (
        "STREAM_MODEL_ALLOW_TEXT=deepseek/deepseek-v4-pro-0813,deepseek/deepseek-v4-flash,qwen/qwen3.8-max"
        in lines
    )
    assert "STREAM_MODEL_ALLOW_VISION=qwen/qwen3.7-flash" in lines
    for key in ("STREAM_MODEL_ALLOW_TEXT", "STREAM_MODEL_ALLOW_VISION"):
        assert [li for li in lines if li.lstrip("#").startswith(key + "=")][0].startswith(key + "=")


def test_the_shipped_template_states_both_stream_ceilings() -> None:
    lines = _read(".env.example").splitlines()

    assert "STREAM_TOKEN_HOURLY_MAX=2000000" in lines
    assert "STREAM_HOURLY_MAX=1200" in lines
