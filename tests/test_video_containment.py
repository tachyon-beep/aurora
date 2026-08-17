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
