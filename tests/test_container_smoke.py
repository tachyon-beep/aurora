import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")
def test_container_invariants():
    result = subprocess.run(
        ["scripts/verify_container.sh"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALL CONTAINER CHECKS PASSED" in result.stdout
    for checkpoint in (
        "agent has the workshop runtime packages",
        "garden contains only two read-only documents",
        "agent state starts empty and is writable",
        "state survives tracked-code recovery",
        "state survives agent container recreation without executing stored code",
        "state marker is absent from other services",
    ):
        assert checkpoint in result.stdout
