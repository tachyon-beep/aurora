from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


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
