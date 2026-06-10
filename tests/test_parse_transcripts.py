import subprocess
import sys


def test_parse_transcripts_runs_help():
    result = subprocess.run(
        [sys.executable, "parse_transcripts.py", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
