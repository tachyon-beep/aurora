import os

import pytest


@pytest.fixture(autouse=True)
def _chdir_repo_root(monkeypatch):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    monkeypatch.chdir(repo_root)
