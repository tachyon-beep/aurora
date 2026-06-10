import agent


def test_read_file_reads_own_source():
    out = agent.read_file()
    assert out.startswith("1: ")
    assert "ToolRegistry" in out


def test_read_file_prefixes_line_numbers(tmp_path, monkeypatch):
    f = tmp_path / "agent.py"
    f.write_text("alpha\nbeta\n", encoding="utf-8")
    monkeypatch.setattr(agent, "_resolve_path", lambda p: str(f))
    assert agent.read_file() == "1: alpha\n2: beta\n"


def test_read_file_missing_returns_error_string(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "_resolve_path", lambda p: str(tmp_path / "nope.py"))
    out = agent.read_file()
    assert out.startswith("error reading file:")


def test_write_file_replaces_a_line(tmp_path, monkeypatch):
    f = tmp_path / "agent.py"
    f.write_text("one\ntwo\nthree\n", encoding="utf-8")
    monkeypatch.setattr(agent, "_resolve_path", lambda p: str(f))
    msg = agent.write_file("replace", 2, "TWO")
    assert "replaced line 2" in msg
    assert f.read_text(encoding="utf-8") == "one\nTWO\nthree\n"


def test_write_file_inserts_a_line(tmp_path, monkeypatch):
    f = tmp_path / "agent.py"
    f.write_text("one\ntwo\n", encoding="utf-8")
    monkeypatch.setattr(agent, "_resolve_path", lambda p: str(f))
    msg = agent.write_file("insert", 2, "MID")
    assert "inserted line 2" in msg
    assert f.read_text(encoding="utf-8") == "one\nMID\ntwo\n"


def test_write_file_deletes_a_line(tmp_path, monkeypatch):
    f = tmp_path / "agent.py"
    f.write_text("one\ntwo\nthree\n", encoding="utf-8")
    monkeypatch.setattr(agent, "_resolve_path", lambda p: str(f))
    msg = agent.write_file("delete", 2)
    assert "deleted line 2" in msg
    assert f.read_text(encoding="utf-8") == "one\nthree\n"


def test_write_file_delete_out_of_range_is_factual(tmp_path, monkeypatch):
    f = tmp_path / "agent.py"
    f.write_text("one\n", encoding="utf-8")
    monkeypatch.setattr(agent, "_resolve_path", lambda p: str(f))
    msg = agent.write_file("delete", 99)
    assert msg == "error: line 99 is out of range; the file has 1 lines"


def test_write_file_rejects_unknown_mode(tmp_path, monkeypatch):
    f = tmp_path / "agent.py"
    f.write_text("one\n", encoding="utf-8")
    monkeypatch.setattr(agent, "_resolve_path", lambda p: str(f))
    msg = agent.write_file("clobber", 1, "x")
    assert msg.startswith("error: unknown mode")
    assert f.read_text(encoding="utf-8") == "one\n"


def test_write_file_out_of_range_is_factual(tmp_path, monkeypatch):
    f = tmp_path / "agent.py"
    f.write_text("one\n", encoding="utf-8")
    monkeypatch.setattr(agent, "_resolve_path", lambda p: str(f))
    msg = agent.write_file("replace", 99, "x")
    assert msg == "error: line 99 is out of range; the file has 1 lines"


def test_write_file_insert_out_of_range_lands_at_end(tmp_path, monkeypatch):
    f = tmp_path / "agent.py"
    f.write_text("one\n", encoding="utf-8")
    monkeypatch.setattr(agent, "_resolve_path", lambda p: str(f))
    msg = agent.write_file("insert", 99, "x")
    assert msg == "inserted line 2 in agent.py"
    assert f.read_text(encoding="utf-8") == "one\nx\n"


def test_write_file_mode_is_an_enum_in_the_schema():
    schema = next(s for s in agent.tools.schemas if s["function"]["name"] == "write_file")
    params = schema["function"]["parameters"]
    assert params["properties"]["mode"]["enum"] == ["replace", "insert", "delete"]
    assert "mode" in params["required"]
    assert "text" not in params["required"]


def test_validate_accepts_valid_python(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("x = 1\n", encoding="utf-8")
    assert agent.validate(str(f)) == "valid"


def test_validate_reports_syntax_error(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def (:\n", encoding="utf-8")
    out = agent.validate(str(f))
    assert out.startswith("SyntaxError at line")
