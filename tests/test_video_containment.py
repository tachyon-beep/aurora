import json
import os
import re
import subprocess

import pytest

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


def _discover_service_names(text):
    """Every top-level service name under `services:`, discovered from the
    compose file's own text rather than hardcoded.

    A hardcoded tuple silently excludes any service added later without a
    matching edit here: every loop below (mount holders, network
    occupants) iterates this collection, so a service missing from it gets
    zero containment coverage instead of a failure. Discovering the names
    from the file removes that failure mode — a synthetic `relay` service
    added to compose is picked up automatically, with no edit required.
    """
    start_match = re.search(r"(?:^|\n)services:\n", text)
    assert start_match, "no top-level services: block"
    start = start_match.end()
    end = text.index("\nnetworks:\n", start)
    body = text[start:end]
    return tuple(re.findall(r"(?:^|\n)  ([A-Za-z0-9_.-]+):\n", body))


SERVICE_NAMES = _discover_service_names(read("docker-compose.yml"))


def test_service_discovery_finds_a_service_not_named_anywhere_else():
    # Proves the discovery function generalizes rather than only working
    # against names it already knows about: a synthetic service name that
    # appears nowhere else in this test file is still found, the same
    # scenario a reviewer used (`relay`, mounting the video volume and
    # carrying a credential) to show the old hardcoded tuple missed a new
    # service silently.
    fake = (
        "services:\n"
        "  recorder:\n"
        "    image: x\n"
        "  relay:\n"
        "    image: y\n"
        "    environment:\n"
        "      RELAY_TOKEN: z\n"
        "networks:\n"
        "  default: {}\n"
    )
    assert _discover_service_names(fake) == ("recorder", "relay")


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
#
# The single property the whole stills-containment argument rests on: the
# outward-facing stage cannot see the video volume. Checked two ways —
# a JSON-based check against compose's own parser (authoritative wherever
# `docker compose` is available: it normalises both mount syntaxes and any
# container path to the same structure) and a text-based fallback so the
# property is still checked in a bare environment. Both match on the
# volume's SOURCE name, not a literal `video:/video` string, so relocating
# the mount to a different container path or writing it in the long-form
# YAML mount syntax (`type: volume, source: video, target: ...`) — which is
# not exotic, it is exactly what `docker compose config` itself emits — is
# still caught.


def _compose_config_json():
    """Parsed `docker compose config --format json`, or None when compose
    is unavailable in this environment."""
    try:
        result = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


@pytest.fixture(scope="module")
def compose_config():
    cfg = _compose_config_json()
    if cfg is None:
        pytest.skip("docker compose config is not available in this environment")
    return cfg


def _named_volume_sources(service_config):
    """The set of named-volume sources a parsed service config mounts, at
    any target path, regardless of which compose mount syntax wrote it."""
    return {
        mount["source"]
        for mount in service_config.get("volumes", [])
        if mount.get("type") == "volume"
    }


def test_the_stage_does_not_mount_the_video_volume_source(compose_config):
    stage = compose_config["services"]["stage"]
    assert "video" not in _named_volume_sources(stage)


def test_the_viewer_does_not_mount_the_video_volume_source(compose_config):
    viewer = compose_config["services"]["viewer"]
    assert "video" not in _named_volume_sources(viewer)


def test_only_the_agent_and_the_video_service_mount_the_video_volume_source(compose_config):
    holders = {
        name
        for name, service in compose_config["services"].items()
        if "video" in _named_volume_sources(service)
    }
    assert holders == {"agent", "video"}


def test_the_agent_mounts_the_video_volume_read_write_by_source(compose_config):
    agent = compose_config["services"]["agent"]
    mounts = [
        m
        for m in agent.get("volumes", [])
        if m.get("type") == "volume" and m.get("source") == "video"
    ]
    assert len(mounts) == 1
    assert mounts[0]["target"] == "/video"
    assert not mounts[0].get("read_only")


def _mounts_named_volume_text(block, volume_name):
    """Best-effort textual check for whether a service block mounts the
    named volume, in either compose mount syntax, at any container path.

    Short form: `- <volume>:<target>[:mode]` — the volume name is the text
    immediately before the first colon. Long form: a list item carrying
    `source: <volume>` on its own line. This is a fallback for
    environments without `docker compose`; the JSON-based checks above are
    authoritative wherever compose is available, since they use compose's
    own parser rather than a regex approximation.
    """
    short_form = re.search(rf"(?:^|\n)[ \t]*-[ \t]*{re.escape(volume_name)}:", block)
    long_form = re.search(rf"\bsource:[ \t]*{re.escape(volume_name)}\b", block)
    return bool(short_form or long_form)


def _named_volume_is_read_only_text(block, volume_name):
    """Best-effort textual read-only check across both mount syntaxes.

    Returns True/False when the mount is found; None when the volume is
    not mounted at all in this block (callers that need "mounted AND
    read-write" should assert the mount separately).
    """
    short = re.search(rf"(?:^|\n)[ \t]*-[ \t]*{re.escape(volume_name)}:([^\n]*)", block)
    if short:
        return short.group(1).rstrip().endswith(":ro")
    long_match = re.search(rf"\bsource:[ \t]*{re.escape(volume_name)}\b", block)
    if long_match:
        tail = block[long_match.end() :]
        next_item = re.search(r"\n[ \t]*-[ \t]*type:", tail)
        window = tail[: next_item.start()] if next_item else tail
        return "read_only: true" in window
    return None


def test_the_stage_does_not_mount_the_video_volume_text_fallback():
    assert not _mounts_named_volume_text(service_block("stage"), "video")


def test_the_viewer_does_not_mount_the_video_volume_text_fallback():
    assert not _mounts_named_volume_text(service_block("viewer"), "video")


def test_only_the_agent_and_the_video_service_mount_the_video_volume_text_fallback():
    holders = {
        name for name in SERVICE_NAMES if _mounts_named_volume_text(service_block(name), "video")
    }
    assert holders == {"agent", "video"}


def test_the_agent_mounts_the_video_volume_read_write_text_fallback():
    block = service_block("agent")
    assert _mounts_named_volume_text(block, "video")
    assert _named_volume_is_read_only_text(block, "video") is False


def test_the_video_volume_is_declared():
    assert re.search(r"^  video: \{\}", read("docker-compose.yml"), re.MULTILINE)


# self-tests for the mount-source helpers: prove they catch both compose
# mount syntaxes at a relocated container path — the two live evasions a
# reviewer demonstrated against an earlier, literal-string version of these
# tests (`- video:/srv/video` in the stage service, and the long-form
# mount `docker compose config` itself emits). Run against synthetic text,
# not the real file, so they hold regardless of `docker compose`
# availability and regardless of what docker-compose.yml says today.


def test_helper_catches_the_short_form_evasion_at_a_relocated_path():
    fake_block = "\n    volumes:\n      - video:/srv/video\n"
    assert _mounts_named_volume_text(fake_block, "video")


def test_helper_catches_the_long_form_evasion():
    fake_block = (
        "\n    volumes:\n"
        "      - type: volume\n"
        "        source: video\n"
        "        target: /video\n"
        "        read_only: true\n"
    )
    assert _mounts_named_volume_text(fake_block, "video")
    assert _named_volume_is_read_only_text(fake_block, "video") is True


def test_helper_does_not_false_positive_on_an_unrelated_volume():
    fake_block = "\n    volumes:\n      - transcripts:/transcripts:ro\n"
    assert not _mounts_named_volume_text(fake_block, "video")


def test_helper_does_not_false_positive_on_a_similarly_named_volume():
    # `video_backup` must not be mistaken for `video`: the colon has to
    # follow the volume name immediately.
    fake_block = "\n    volumes:\n      - video_backup:/anything\n"
    assert not _mounts_named_volume_text(fake_block, "video")


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


def test_env_example_does_not_document_a_dead_variable():
    # VIDEO_STILL_KEEP is a hardcoded module constant in video.py, never
    # read from os.environ or passed through compose's environment: block.
    # A documented knob that does nothing is worse than no knob.
    text = read(".env.example")
    assert "VIDEO_STILL_KEEP" not in text


# live-stack verification


def test_verify_script_checks_the_video_surface():
    text = read("scripts/verify_container.sh")
    assert "/video is present in the agent and writable" in text
    assert "video holds no credential" in text
