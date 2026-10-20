"""Use case definitions: input schema, references and structural checks (M2.3a)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from forge.usecases import definition as d
from forge.usecases.schemas import UseCaseBody, UseCaseCreateIn, UseCaseInputField


def test_input_schema_is_generated_from_the_form() -> None:
    schema = d.input_schema(
        [
            {"name": "topic", "label": "Topic", "type": "text", "required": True, "help": "What"},
            {"name": "level", "type": "select", "options": ["a", "b"], "default": "a"},
            {"name": "count", "type": "number", "min": 1, "max": 5},
            {"name": "doc", "type": "file", "required": True},
        ]
    )
    assert schema["required"] == ["topic", "doc"]
    assert schema["additionalProperties"] is False
    props = schema["properties"]
    assert props["topic"]["description"] == "What"
    assert props["level"]["enum"] == ["a", "b"]
    assert props["level"]["default"] == "a"
    assert props["count"]["minimum"] == 1
    assert props["count"]["maximum"] == 5
    assert props["doc"]["format"] == "forge-file"


@pytest.mark.parametrize(
    "field",
    [
        {"name": "Bad", "type": "text"},
        {"name": "x", "type": "select"},
        {"name": "x", "type": "text", "options": ["a"]},
        {"name": "x", "type": "select", "options": ["a"], "default": "b"},
        {"name": "x", "type": "number", "default": "1"},
        {"name": "x", "type": "file", "default": "f"},
        {"name": "x", "type": "number", "min": 5, "max": 1},
    ],
)
def test_bad_input_fields_are_refused(field: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        UseCaseInputField.model_validate(field)


def test_body_consistency() -> None:
    with pytest.raises(ValidationError):
        UseCaseBody(
            input_fields=[
                UseCaseInputField(name="a", type="text"),
                UseCaseInputField(name="a", type="text"),
            ]
        )
    with pytest.raises(ValidationError):
        UseCaseBody(output_type="json")
    with pytest.raises(ValidationError):
        UseCaseBody(output_type="markdown", output_schema={"type": "object"})
    with pytest.raises(ValidationError):
        UseCaseBody(category_id="00000000-0000-7000-8000-000000000001")
    with pytest.raises(ValidationError):
        UseCaseCreateIn(name="bad/name", pattern="graph")
    assert UseCaseBody(output_type="json", output_schema={"type": "object"}).output_type == "json"


def test_references_are_extracted_from_both_patterns() -> None:
    team = {"agents": [{"agent_ref": "Writer@2", "task": "t"}, {"agent_ref": "qa@1"}]}
    refs, issues = d.extract_refs("supervisor", team)
    assert [(r.kind, r.name, r.version) for r in refs] == [
        ("agent", "Writer", 2),
        ("agent", "qa", 1),
    ]
    assert not issues
    graph = {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "a", "type": "agent", "agent_ref": "writer@3"},
            {"id": "t", "type": "tool", "tool_ref": "lookup@1"},
            {"id": "l", "type": "llm", "prompt_ref": "summary@4"},
            {"id": "bad", "type": "agent", "agent_ref": "no-version"},
        ]
    }
    refs, issues = d.extract_refs("graph", graph)
    assert [(r.kind, r.node) for r in refs] == [("agent", "a"), ("tool", "t"), ("prompt", "l")]
    assert [i.code for i in issues] == ["bad_reference"]
    assert issues[0].node == "bad"


def codes(pattern: str, definition: dict[str, object]) -> list[str]:
    return [i.code for i in d.validate_definition(pattern, definition)]


def test_structural_validation() -> None:
    assert codes("graph", {}) == []  # an empty draft is allowed
    assert codes("supervisor", {"agents": []}) == []
    assert codes("graph", {"pattern": "supervisor"}) == ["pattern_mismatch"]
    assert codes("supervisor", {"agents": [{"task": "x"}]}) == ["bad_agent"]
    nodes = [
        {"id": "start", "type": "start"},
        {"id": "start", "type": "end"},
        {"id": "Bad", "type": "agent"},
        {"id": "x", "type": "nope"},
        {"id": "s2", "type": "start"},
    ]
    assert codes("graph", {"nodes": nodes}) == [
        "duplicate_node",
        "bad_node_id",
        "bad_node_type",
        "multiple_start",
    ]
    assert codes("graph", {"nodes": "x"}) == ["bad_nodes"]


def test_completeness() -> None:
    assert [i.code for i in d.completeness("supervisor", {})] == ["empty_workflow"]
    assert d.completeness("supervisor", {"agents": [{"agent_ref": "a@1"}]}) == []
    assert [i.code for i in d.completeness("graph", {"nodes": []})] == [
        "no_start",
        "empty_workflow",
    ]
    ok = {"nodes": [{"id": "start", "type": "start"}, {"id": "a", "type": "agent"}]}
    assert d.completeness("graph", ok) == []


def test_ui_block_is_ignored_by_validation() -> None:
    graph = {
        "nodes": [{"id": "start", "type": "start"}],
        "ui": {"positions": {"start": {"x": 1, "y": 2}}},
    }
    assert d.validate_definition("graph", graph) == []


def test_references_accept_any_asset_name() -> None:
    refs, issues = d.extract_refs(
        "supervisor", {"agents": [{"agent_ref": "Agent 1f (imported)@3"}, {"agent_ref": "a@b"}]}
    )
    assert [(r.name, r.version) for r in refs] == [("Agent 1f (imported)", 3)]
    assert [i.code for i in issues] == ["bad_reference"]
