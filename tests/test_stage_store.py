import pytest

from stage import store


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("STAGE_STATE_DIR", str(tmp_path))
    yield tmp_path


def test_save_and_load_round_trip(tmp_path):
    assert store.save("sample", {"a": 1, "b": ["x"]}) is True
    assert store.load("sample") == {"a": 1, "b": ["x"]}


def test_load_of_a_missing_file_is_none():
    assert store.load("absent") is None


def test_load_of_garbage_is_none(tmp_path):
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert store.load("bad") is None


def test_load_of_a_non_object_document_is_none(tmp_path):
    (tmp_path / "list.json").write_text("[1, 2]", encoding="utf-8")
    assert store.load("list") is None


def test_save_into_a_missing_directory_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("STAGE_STATE_DIR", str(tmp_path / "nowhere"))
    assert store.save("sample", {"a": 1}) is False


def test_an_oversized_payload_is_refused(tmp_path):
    assert store.save("big", {"blob": "x" * (store.MAX_STATE_BYTES + 1)}) is False
    assert store.load("big") is None


def test_an_oversized_file_on_disk_is_not_loaded(tmp_path):
    (tmp_path / "grown.json").write_text(
        '{"blob": "' + "x" * (store.MAX_STATE_BYTES + 1) + '"}', encoding="utf-8"
    )
    assert store.load("grown") is None


def test_a_failed_save_leaves_the_previous_document(tmp_path):
    assert store.save("keep", {"v": 1}) is True
    assert store.save("keep", {"blob": "x" * (store.MAX_STATE_BYTES + 1)}) is False
    assert store.load("keep") == {"v": 1}


def test_an_unserialisable_payload_is_refused(tmp_path):
    assert store.save("odd", {"v": object()}) is False
