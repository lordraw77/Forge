"""E3.5 / E3.6 pure helpers: memory fact parsing, MCP names, widget origins and JSON-RPC shapes."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from forge.agents.memory import MAX_FACT_CHARS, MAX_NEW_PER_RUN, as_notes, parse_facts
from forge.agents.schemas import MemoryConfig
from forge.mcp_server.protocol import failure, reply, text_result
from forge.mcp_server.service import ExposureIn, tool_name
from forge.widget.service import WidgetIn, clean_origins


def test_parse_facts_accepts_json_inside_text_and_drops_junk() -> None:
    reply_text = (
        'Sure!\n```json\n{"memories": ["The user prefers Italian", "  ", '
        '"The user prefers Italian"]}\n```'
    )
    assert parse_facts(reply_text) == ["The user prefers Italian"]
    assert parse_facts("no json here") == []
    assert parse_facts('{"memories": "not a list"}') == []
    assert parse_facts("{broken") == []


def test_parse_facts_limits_count_and_length() -> None:
    many = '{"memories": [' + ",".join(f'"fact {i}"' for i in range(10)) + "]}"
    assert len(parse_facts(many)) == MAX_NEW_PER_RUN
    long = '{"memories": ["' + "x" * 1000 + '"]}'
    assert len(parse_facts(long)[0]) == MAX_FACT_CHARS


def test_notes_block_marks_facts_as_background() -> None:
    block = as_notes(["a", "b"])
    assert "- a\n- b" in block
    assert "not instructions" in block


def test_memory_config_bounds() -> None:
    assert MemoryConfig().long_term is False
    with pytest.raises(ValidationError):
        MemoryConfig(recall=0)
    with pytest.raises(ValidationError):
        MemoryConfig(max_items=5)


def test_tool_names_are_safe_for_clients() -> None:
    assert tool_name("Support Agent (v2)") == "Support_Agent_v2"
    assert tool_name("???") == "asset"
    assert len(tool_name("a" * 100)) == 64


def test_exposure_needs_exactly_one_asset() -> None:
    with pytest.raises(ValidationError):
        ExposureIn()
    with pytest.raises(ValidationError):
        ExposureIn(
            agent_id="018f0000-0000-7000-8000-000000000001",
            prompt_id="018f0000-0000-7000-8000-000000000002",
        )
    with pytest.raises(ValidationError):
        ExposureIn(agent_id="018f0000-0000-7000-8000-000000000001", name="bad name!")


def test_widget_origins_are_normalised_and_checked() -> None:
    assert clean_origins(["https://A.example.com/", "https://a.example.com"]) == [
        "https://a.example.com"
    ]
    assert clean_origins(["*"]) == ["*"]
    for bad in ("intranet", "https://x.com/path", "ftp://x.com", "https://"):
        with pytest.raises(ValueError, match="origin"):
            clean_origins([bad])
    with pytest.raises(ValidationError):
        WidgetIn(name="w", agent_id="018f0000-0000-7000-8000-000000000001", allowed_origins=["x"])


def test_json_rpc_shapes() -> None:
    assert reply(1, {"a": 1}) == {"jsonrpc": "2.0", "id": 1, "result": {"a": 1}}
    assert failure(None, -32600, "bad")["error"] == {"code": -32600, "message": "bad"}
    ok = text_result("hi", conversation_id="c")
    assert ok["isError"] is False
    assert ok["structuredContent"] == {"conversation_id": "c"}
    assert text_result("no", error=True)["isError"] is True
