"""Tests for the per-edit diff excerpts over the mirrored agent source."""

import os
import time

import pytest

from stage import codewatch

SECRET = "OUTSIDE_THE_MOUNT_c0ffee"


@pytest.fixture(autouse=True)
def _clean_codewatch_state():
    codewatch._reset_for_tests()
    yield
    codewatch._reset_for_tests()


def _work_dir(tmp_path, source="a\nb\nc\n"):
    work = tmp_path / "work"
    work.mkdir()
    _rewrite(work, source, stamp=1)
    return work


def _rewrite(work, source, stamp):
    path = work / "agent.py"
    path.write_text(source, encoding="utf-8")
    os.utime(path, ns=(stamp * 10**9, stamp * 10**9))


def test_first_call_records_state_and_returns_none(tmp_path):
    work = _work_dir(tmp_path)

    assert codewatch.latest_edit(str(work)) is None


def test_missing_source_returns_none(tmp_path):
    work = tmp_path / "work"
    work.mkdir()

    assert codewatch.latest_edit(str(work)) is None


def test_second_call_reports_the_edit(tmp_path):
    work = _work_dir(tmp_path, "a\nb\nc\n")
    assert codewatch.latest_edit(str(work)) is None
    _rewrite(work, "a\nB\nc\nd\n", stamp=2)

    edit = codewatch.latest_edit(str(work), now=123.0)

    assert edit["epoch"] == 123.0
    assert edit["added"] == 2
    assert edit["removed"] == 1
    lines = edit["excerpt"].splitlines()
    assert any(line.startswith("@@") for line in lines)
    assert "+B" in lines
    assert "-b" in lines
    assert not any(line.startswith("+++") or line.startswith("---") for line in lines)


def test_epoch_defaults_to_observation_time(tmp_path):
    work = _work_dir(tmp_path, "a\n")
    codewatch.latest_edit(str(work))
    _rewrite(work, "a\nb\n", stamp=2)

    before = time.time()
    edit = codewatch.latest_edit(str(work))
    after = time.time()

    assert before <= edit["epoch"] <= after


def test_symlink_out_returns_none_and_does_not_poison_state(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    secret = tmp_path / "outside" / "agent.py"
    secret.parent.mkdir()
    secret.write_text(SECRET + "\n", encoding="utf-8")
    (work / "agent.py").symlink_to(secret)

    assert codewatch.latest_edit(str(work)) is None

    (work / "agent.py").unlink()
    _rewrite(work, "a\n", stamp=5)
    assert codewatch.latest_edit(str(work)) is None
    _rewrite(work, "a\nb\n", stamp=6)

    edit = codewatch.latest_edit(str(work))

    assert SECRET not in edit["excerpt"]
    assert edit["added"] == 1
    assert edit["removed"] == 0
    assert "+b" in edit["excerpt"].splitlines()


def test_excerpt_line_and_total_caps(tmp_path):
    old = "\n".join(f"a{i}" for i in range(50)) + "\n"
    new = "\n".join("y" * 300 + str(i) for i in range(50)) + "\n"
    work = _work_dir(tmp_path, old)
    codewatch.latest_edit(str(work))
    _rewrite(work, new, stamp=2)

    edit = codewatch.latest_edit(str(work))

    assert edit["added"] == 50
    assert edit["removed"] == 50
    lines = edit["excerpt"].splitlines()
    assert len(lines) <= codewatch.EXCERPT_LINES
    assert all(len(line) <= codewatch.EXCERPT_LINE_CHARS for line in lines)
    assert len(edit["excerpt"]) <= codewatch.EXCERPT_CAP


def test_edit_persists_until_the_next_change_replaces_it(tmp_path):
    work = _work_dir(tmp_path, "a\n")
    codewatch.latest_edit(str(work))
    _rewrite(work, "a\nb\n", stamp=2)

    first = codewatch.latest_edit(str(work), now=10.0)
    again = codewatch.latest_edit(str(work), now=20.0)

    assert again == first

    _rewrite(work, "a\nb\nc\n", stamp=3)
    second = codewatch.latest_edit(str(work), now=30.0)

    assert second["epoch"] == 30.0
    assert "+c" in second["excerpt"].splitlines()
    assert "+b" not in second["excerpt"].splitlines()


def test_a_touched_but_identical_file_keeps_the_previous_edit(tmp_path):
    work = _work_dir(tmp_path, "a\n")
    codewatch.latest_edit(str(work))
    _rewrite(work, "a\nb\n", stamp=2)
    first = codewatch.latest_edit(str(work), now=10.0)
    _rewrite(work, "a\nb\n", stamp=3)

    assert codewatch.latest_edit(str(work), now=20.0) == first


def test_a_change_back_to_the_seed_is_marked_restored(tmp_path):
    """The harness rewrites agent.py to the seed after a death and on recovery;
    the observed change is recorded but marked restored so the page names the
    outcome rather than attributing an edit to the agent."""
    work = _work_dir(tmp_path, source="a\nb\nc\nEXTRA\n")
    (work / "agent_stock.py").write_text("a\nb\nc\n", encoding="utf-8")
    assert codewatch.latest_edit(str(work)) is None

    _rewrite(work, "a\nb\nc\n", stamp=2)
    edit = codewatch.latest_edit(str(work))
    assert edit["restored"] is True
    assert edit["removed"] == 1 and edit["added"] == 0

    _rewrite(work, "a\nb\nc\nNEW\n", stamp=3)
    edit = codewatch.latest_edit(str(work))
    assert edit["restored"] is False
    assert edit["added"] == 1


def test_a_source_line_beginning_with_dashes_still_counts(tmp_path):
    work = _work_dir(tmp_path, source="a\n--x\nb\n")
    assert codewatch.latest_edit(str(work)) is None

    _rewrite(work, "a\nb\n++y\n", stamp=2)
    edit = codewatch.latest_edit(str(work))
    assert edit["removed"] == 1
    assert edit["added"] == 1
    assert "---x" in edit["excerpt"]
    assert "+++y" in edit["excerpt"]
