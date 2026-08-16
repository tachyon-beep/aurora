import os
import time

import pytest

from stage import records


@pytest.fixture(autouse=True)
def _clean_records_state():
    records._reset_for_tests()
    yield
    records._reset_for_tests()


def _tombstone(work, name, text, age_seconds):
    tomb = work / "tombstones"
    tomb.mkdir(parents=True, exist_ok=True)
    path = tomb / name
    path.write_text(text, encoding="utf-8")
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


def test_empty_dir_yields_zeros_and_nones(tmp_path):
    book = records.record_book(str(tmp_path))
    assert book == {
        "lives_ended": 0,
        "chose": 0,
        "longest_life": None,
        "most_recent_gap_seconds": None,
        "lives": [],
    }


def test_three_tombstones_give_longest_life_gap_and_chose(tmp_path):
    _tombstone(tmp_path, "incarnation-a.txt", "done() at turn 3. more detail.", 1000)
    _tombstone(tmp_path, "incarnation-b.txt", "terminated by the harness after a fault.", 400)
    _tombstone(tmp_path, "incarnation-c.txt", "done() reached. closing note.", 100)
    book = records.record_book(str(tmp_path))
    assert book["lives_ended"] == 3
    assert book["chose"] == 2
    assert book["longest_life"]["ordinal"] == 2
    assert book["longest_life"]["seconds"] == pytest.approx(600, abs=1)
    assert book["most_recent_gap_seconds"] == pytest.approx(300, abs=1)


def test_ordinal_one_is_excluded_from_longest_life(tmp_path):
    _tombstone(tmp_path, "incarnation-a.txt", "first note.", 500)
    _tombstone(tmp_path, "incarnation-b.txt", "second note.", 200)
    book = records.record_book(str(tmp_path))
    assert book["longest_life"]["ordinal"] == 2
    assert book["longest_life"]["seconds"] == pytest.approx(300, abs=1)


def test_undatable_death_yields_no_span_across_it(tmp_path):
    _tombstone(tmp_path, "incarnation-a.txt", "first note.", 1000)
    _tombstone(tmp_path, "incarnation-b.txt", "second note.", 90 * 86400)
    _tombstone(tmp_path, "incarnation-c.txt", "third note.", 100)
    book = records.record_book(str(tmp_path))
    assert book["lives_ended"] == 3
    assert book["longest_life"] is None
    assert book["most_recent_gap_seconds"] is None


def test_empty_tombstone_text_is_not_counted_as_chose(tmp_path):
    _tombstone(tmp_path, "incarnation-a.txt", "", 100)
    book = records.record_book(str(tmp_path))
    assert book["lives_ended"] == 1
    assert book["chose"] == 0


def test_symlinked_tombstone_outside_the_mirror_is_ignored(tmp_path):
    work = tmp_path / "work"
    _tombstone(work, "incarnation-a.txt", "done() reached.", 100)
    outside = tmp_path / "outside.txt"
    outside.write_text("done() reached.", encoding="utf-8")
    (work / "tombstones" / "incarnation-b.txt").symlink_to(outside)
    book = records.record_book(str(work))
    assert book["lives_ended"] == 1
    assert book["chose"] == 1


def test_classification_reads_only_the_head_of_a_long_note(tmp_path):
    text = "terminated by the harness at turn 9. " + "x" * 10_000
    _tombstone(tmp_path, "incarnation-a.txt", text, 100)
    book = records.record_book(str(tmp_path))
    assert book["chose"] == 0


def test_the_book_is_memoized_on_the_tombstone_set(tmp_path, monkeypatch):
    work = tmp_path / "work"
    tombs = work / "tombstones"
    tombs.mkdir(parents=True)
    now = time.time()
    for ordinal, age in ((1, 3000), (2, 2000)):
        path = tombs / f"incarnation-{ordinal:04d}.txt"
        path.write_text("Incarnation ended by done().", encoding="utf-8")
        os.utime(path, (now - age, now - age))
    reads = []
    real_open = open

    def counting_open(path, *args, **kwargs):
        reads.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", counting_open)
    first = records.record_book(str(work), now=now)
    assert first["lives_ended"] == 2 and first["chose"] == 2
    assert len(reads) == 2
    second = records.record_book(str(work), now=now)
    assert second == first
    assert len(reads) == 2

    third_path = tombs / "incarnation-0003.txt"
    third_path.write_text("Incarnation terminated by the harness.", encoding="utf-8")
    os.utime(third_path, (now - 1000, now - 1000))
    third = records.record_book(str(work), now=now)
    assert third["lives_ended"] == 3 and third["chose"] == 2
    assert len(reads) == 5


def test_lives_lists_every_tombstone_oldest_first_with_kind_and_span(tmp_path):
    _tombstone(tmp_path, "incarnation-a.txt", "done() at turn 3. more detail.", 1000)
    _tombstone(tmp_path, "incarnation-b.txt", "terminated by the harness after a fault.", 400)
    _tombstone(tmp_path, "incarnation-c.txt", "done() reached. closing note.", 100)
    lives = records.record_book(str(tmp_path))["lives"]
    assert [life["ordinal"] for life in lives] == [1, 2, 3]
    assert [life["kind"] for life in lives] == ["declared", "harness", "declared"]
    assert lives[0]["seconds"] is None, "the first life has no recorded birth"
    assert lives[1]["seconds"] == pytest.approx(600, abs=1)
    assert lives[2]["seconds"] == pytest.approx(300, abs=1)
    assert lives[2]["ended_epoch"] == pytest.approx(time.time() - 100, abs=2)


def test_lives_is_empty_for_an_empty_dir(tmp_path):
    assert records.record_book(str(tmp_path))["lives"] == []


def test_an_undatable_death_gives_no_span_on_either_side(tmp_path):
    _tombstone(tmp_path, "incarnation-a.txt", "first note.", 1000)
    _tombstone(tmp_path, "incarnation-b.txt", "second note.", 90 * 86400)
    _tombstone(tmp_path, "incarnation-c.txt", "third note.", 100)
    lives = records.record_book(str(tmp_path))["lives"]
    assert lives[1]["seconds"] is None and lives[1]["ended_epoch"] is None
    assert lives[2]["seconds"] is None
