from typing import List
import agent


def test_schema_basic_shape_and_required():
    reg = agent.ToolRegistry()

    @reg.register
    def sample(a: str, b: int = 3) -> str:
        """Do a sample thing.

        Args:
            a: the first parameter.
            b: the second parameter.
        """
        return ""

    schema = reg.schemas[0]
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "sample"
    assert fn["description"] == "Do a sample thing."
    props = fn["parameters"]["properties"]
    assert props["a"] == {"type": "string", "description": "the first parameter."}
    assert props["b"] == {"type": "integer", "description": "the second parameter."}
    assert fn["parameters"]["required"] == ["a"]


def test_schema_list_and_dict_types():
    reg = agent.ToolRegistry()

    @reg.register
    def collections(items: List[int], mapping: dict) -> str:
        """Handle collections."""
        return ""

    props = reg.schemas[0]["function"]["parameters"]["properties"]
    assert props["items"]["type"] == "array"
    assert props["items"]["items"] == {"type": "integer"}
    assert props["mapping"]["type"] == "object"


def test_schema_bool_type():
    reg = agent.ToolRegistry()

    @reg.register
    def flagged(on: bool) -> str:
        """Toggle something."""
        return ""

    props = reg.schemas[0]["function"]["parameters"]["properties"]
    assert props["on"]["type"] == "boolean"


def test_every_registered_tool_has_a_nonempty_description():
    for schema in agent.tools.schemas:
        assert schema["function"]["description"].strip()


def test_genesis_surface_is_exactly_eight_tools_in_order():
    assert list(agent.tools.tools) == [
        "read_file",
        "write_file",
        "validate",
        "migrate",
        "done",
        "reset",
        "list_dir",
        "compact",
    ]
    assert [s["function"]["name"] for s in agent.tools.schemas] == list(agent.tools.tools)
