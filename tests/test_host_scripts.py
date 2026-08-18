import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _foreign_owned_directory() -> str:
    """An existing directory owned by a uid other than the sense service's 1000."""
    for candidate in ("/usr", "/etc", "/var/tmp", "/tmp"):
        try:
            owner = os.stat(candidate).st_uid
        except OSError:
            continue
        if os.path.isdir(candidate) and owner != 1000:
            return candidate
    return ""


def test_build_image_is_formatted_without_discarding_the_allocation() -> None:
    # mkfs.ext4 discards the blocks fallocate reserved unless nodiscard is set,
    # which would turn the preallocated image sparse and remove the fixed
    # footprint the script's header promises.
    script = _read("scripts/create_build_volume.sh")
    mkfs = next(line for line in script.splitlines() if "mkfs.ext4" in line)

    assert "nodiscard" in mkfs
    assert "fallocate" in script


def test_sense_script_refuses_a_directory_the_service_cannot_write() -> None:
    if os.geteuid() == 0:
        pytest.skip("running as root: the script would chown the candidate directory")
    directory = _foreign_owned_directory()
    if not directory:
        pytest.skip("no existing directory owned by a uid other than 1000")

    result = subprocess.run(
        ["sh", str(ROOT / "scripts" / "create_sense_volume.sh")],
        env={**os.environ, "AURORA_SENSE_DIR": directory},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert f"chown 1000:1000 {directory}" in output


def test_sense_script_accepts_a_directory_the_service_can_write(tmp_path) -> None:
    directory = tmp_path / "sense"
    if os.stat(tmp_path).st_uid != 1000:
        pytest.skip("temporary directories are not owned by uid 1000 on this host")

    result = subprocess.run(
        ["sh", str(ROOT / "scripts" / "create_sense_volume.sh")],
        env={**os.environ, "AURORA_SENSE_DIR": str(directory)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert directory.is_dir()


def test_build_wipe_restores_permissions_before_removing() -> None:
    # The agent owns /build and can leave a directory with no traversal bits;
    # rm cannot descend into one, so the modes are restored first.
    script = _read("entrypoint.sh")
    chmod = script.index("chmod -R u+rwX /build")

    assert chmod < script.index("find /build")


def test_fetch_corpus_adds_history_without_provider_prose() -> None:
    script = _read("scripts/fetch_corpus.sh")

    # Each historical series terminates in a live diode instrument; all three
    # must be present for the verification loop the corpus is being grown for.
    assert "SN_d_tot_V2.0.csv" in script
    assert "ghcnd-stations.txt" in script
    assert "earthquake.usgs.gov/fdsnws/event" in script

    # Bare data only: no provider documentation may land under /corpus.
    for artefact in ("readme", "README", "LICENSE", "index.html"):
        assert f'{artefact}"' not in script.replace("$OUT", "")


def test_fetch_corpus_adds_place_sky_and_life_in_readable_formats() -> None:
    script = _read("scripts/fetch_corpus.sh")

    assert "cities500.zip" in script
    assert "ne_10m_coastline.geojson" in script
    assert "NGC.csv" in script
    assert "dna.toplevel.fa.gz" in script
    assert "bach-370-chorales" in script
    assert "ETOPO_2022_v1_60s" in script
    assert "etopo_5min.nc" in script

    # The 457 MB original must never reach /corpus: only the subsample is kept.
    assert 'rm -rf "$tmp"' in script

    # Every added set must be readable with the standard library, numpy, or
    # one of the four approved packages.
    # GeoTIFF and shapefile still have no reader on the agent image; shipping
    # one would create a surface the agent cannot open. netCDF has left this
    # list: netCDF4 was admitted for exactly that purpose (spec 4.7).
    for unreadable in (".tif", ".tiff", ".shp"):
        assert unreadable not in script


def test_fetch_corpus_keeps_notation_without_audio_or_renderings() -> None:
    script = _read("scripts/fetch_corpus.sh")

    # Music ships as notation only. MIDI would smuggle a sound representation
    # into a wave that deliberately excludes one, and the rendered PDFs are
    # redundant with the notation they were rendered from.
    kern_section = script.split("== notation")[1]
    assert "-name '*.krn'" in kern_section
    assert "*.mid" not in kern_section.replace("! -name '*.krn'", "")
    assert "-delete" in kern_section
