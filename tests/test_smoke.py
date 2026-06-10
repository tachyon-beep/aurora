def test_agent_imports_without_side_effects():
    import agent

    assert hasattr(agent, "tools")
    assert "read_file" in agent.tools.tools


def test_agent_py_is_valid_python():
    import ast

    ast.parse(open("agent.py", encoding="utf-8").read())
