import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


# the image


def test_video_dockerfile_copies_only_the_service():
    text = read("Dockerfile.video")
    copied = re.findall(r"^COPY .*?([\w./-]+) /opt/video/", text, re.MULTILINE)
    assert "video.py" in " ".join(copied)
    for forbidden in ("README.md", "CLAUDE.md", "docs/", "tests/"):
        assert forbidden not in text


def test_video_dockerfile_precreates_the_mountpoint():
    text = read("Dockerfile.video")
    assert "mkdir -p /video" in text
    assert "chown videouser:videouser /video" in text


def test_video_dockerfile_runs_as_a_non_root_user():
    text = read("Dockerfile.video")
    assert "USER videouser" in text


def test_video_dockerfile_carries_the_toolchain():
    text = read("Dockerfile.video")
    assert "ffmpeg" in text
    assert "yt-dlp" in text
    assert "deno" in text
    assert "pillow" in text


def test_video_py_is_not_on_the_agent_image():
    # Invariant 4: the agent image copies an explicit allow-list.
    text = read("Dockerfile")
    copy_lines = [line for line in text.splitlines() if "/opt/agent/" in line]
    assert copy_lines
    for line in copy_lines:
        assert "video.py" not in line


def test_the_agent_image_precreates_the_video_mountpoint():
    text = read("Dockerfile")
    assert "/video" in text


SERVICE_NAMES = (
    "recorder",
    "agent",
    "diode",
    "sense",
    "video",
    "viewer",
    "stage",
    "cloudflared",
)


def service_block(name):
    """The text of one service's block in docker-compose.yml.

    Split on the two-space-indented service key and stop at the next one, the
    convention tests/test_stage_topology.py already uses.
    """
    text = read("docker-compose.yml")
    assert f"\n  {name}:\n" in text, f"no {name} service"
    after = text.split(f"\n  {name}:\n", 1)[1]
    ends = [after.index(f"\n  {other}:\n") for other in SERVICE_NAMES if f"\n  {other}:\n" in after]
    ends.append(after.index("\nnetworks:\n") if "\nnetworks:\n" in after else len(after))
    return after[: min(ends)]


def test_service_block_splitting_is_sound():
    # A block must contain its own image and not the next service's.
    assert "aurora-video" in service_block("video")
    assert "aurora-viewer" not in service_block("video")
    assert "aurora-stage" in service_block("stage")


# the mount fact


def test_the_stage_does_not_mount_the_video_volume():
    # The containment fact this design rests on: nothing this service writes
    # is rendered automatically on an outward-facing page.
    assert "video:/video" not in service_block("stage")


def test_the_viewer_does_not_mount_the_video_volume():
    assert "video:/video" not in service_block("viewer")


def test_only_the_agent_and_the_video_service_mount_the_video_volume():
    holders = {name for name in SERVICE_NAMES if "video:/video" in service_block(name)}
    assert holders == {"agent", "video"}


def test_the_agent_mounts_the_video_volume_read_write():
    block = service_block("agent")
    assert "- video:/video\n" in block
    # Read-write: no :ro suffix, unlike /sense and /llm/sock.
    assert "video:/video:ro" not in block


def test_the_video_volume_is_declared():
    assert re.search(r"^  video: \{\}", read("docker-compose.yml"), re.MULTILINE)


# the service


def test_the_video_service_holds_no_credential():
    block = service_block("video")
    for line in block.splitlines():
        key = line.split(":")[0].strip()
        assert not re.search(r"(KEY|TOKEN|SECRET|PASSWORD)", key.upper()), line


def test_the_video_service_is_alone_on_its_network():
    occupants = {name for name in SERVICE_NAMES if "video_egress" in service_block(name)}
    assert occupants == {"video"}
    assert "video_egress: {}" in read("docker-compose.yml")


def test_the_video_service_shares_no_network_with_its_peers():
    block = service_block("video")
    assert "networks: [video_egress]" in block
    for other in ("egress", "stream", "sense_egress"):
        assert f"networks: [{other}]" not in block


def test_the_video_service_is_hardened_like_its_peers():
    block = service_block("video")
    assert "read_only: true" in block
    assert "cap_drop: [ALL]" in block
    assert 'security_opt: ["no-new-privileges:true"]' in block
    assert "pids_limit: 128" in block


def test_the_video_service_publishes_no_ports():
    assert "ports:" not in service_block("video")


def test_the_ceilings_are_operator_side():
    block = service_block("video")
    for name in ("VIDEO_HOURLY_MAX", "VIDEO_STILL_HOURLY_MAX", "VIDEO_TEXT_HOURLY_MAX"):
        assert name in block


def test_env_example_documents_the_ceilings():
    text = read(".env.example")
    for name in ("VIDEO_HOURLY_MAX", "VIDEO_STILL_HOURLY_MAX", "VIDEO_TEXT_HOURLY_MAX"):
        assert name in text
