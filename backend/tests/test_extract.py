"""Tests for the repair loop, using a stub model instead of a real one.

No API key, no network, no cost. A fake model that returns scripted responses
lets us test the loop's behaviour precisely, including failure modes that would
be hard to provoke on purpose with a real one.

This is a standard technique: keep the provider behind a seam, then substitute
a predictable stand-in for it in tests.
"""

import json

from app.extract import extract_process
from app.schemas.process import ProcessGraph


class ScriptedLLM:
    """Returns prepared responses in order, and records what it was asked."""

    name = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def generate_json(self, *, system: str, prompt: str, schema: dict) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0) if self._responses else "{}"


def graph_dict(*, broken: bool = False) -> dict:
    """A small invoice process. `broken` flattens the decision, as models do."""

    edges = [
        {"from_step": "find_invoice", "to_step": "check_amount"},
        {"from_step": "check_amount", "to_step": "ask_manager", "condition": "over 5000"},
        {"from_step": "check_amount", "to_step": "update_sheet", "condition": "5000 or under"},
        {"from_step": "ask_manager", "to_step": "update_sheet"},
    ]
    if broken:
        edges = [
            {"from_step": "find_invoice", "to_step": "check_amount"},
            {"from_step": "check_amount", "to_step": "update_sheet"},
        ]

    return {
        "title": "Invoice intake",
        "summary": "Invoices arrive by email and end up in a spreadsheet.",
        "trigger": {
            "kind": "schedule",
            "description": "Every morning",
            "schedule_hint": "weekday mornings",
            "first_step_id": "find_invoice",
        },
        "systems": [],
        "steps": [
            {"id": "find_invoice", "name": "Find the invoice", "description": "...", "kind": "read"},
            {"id": "check_amount", "name": "Check the amount", "description": "...", "kind": "decision"},
            {"id": "ask_manager", "name": "Ask the manager", "description": "...", "kind": "judgement"},
            {"id": "update_sheet", "name": "Update the sheet", "description": "...", "kind": "write"},
        ],
        "edges": edges,
        "questions": [],
    }


def test_a_good_first_answer_is_accepted_without_a_retry():
    llm = ScriptedLLM([json.dumps(graph_dict())])
    result = extract_process("...", llm)

    assert result.ok
    assert isinstance(result.graph, ProcessGraph)
    assert len(result.attempts) == 1
    assert result.attempts[0].ok


def test_a_flattened_decision_is_caught_and_repaired():
    llm = ScriptedLLM([json.dumps(graph_dict(broken=True)), json.dumps(graph_dict())])
    result = extract_process("...", llm)

    assert result.ok
    assert len(result.attempts) == 2
    assert not result.attempts[0].ok
    assert result.attempts[1].ok


def test_the_repair_prompt_carries_the_faults_and_the_previous_answer():
    llm = ScriptedLLM([json.dumps(graph_dict(broken=True)), json.dumps(graph_dict())])
    extract_process("I check emails for invoices.", llm)

    repair = llm.prompts[1]
    assert "needs at least two" in repair          # the objection
    assert "check_amount" in repair                 # its own previous output
    assert "I check emails for invoices." in repair # the original description


def test_it_stops_early_when_the_same_fault_comes_back():
    """Repeating the same complaint costs quota and changes nothing."""

    broken = json.dumps(graph_dict(broken=True))
    llm = ScriptedLLM([broken, broken, broken, broken])
    result = extract_process("...", llm, max_attempts=3)

    assert not result.ok
    assert result.graph is None
    assert len(result.attempts) == 2  # tried the repair once, saw no change, stopped


def test_it_uses_its_full_budget_while_the_faults_keep_changing():
    """A different fault each time means the repair is landing, so keep going."""

    no_steps = json.dumps({**graph_dict(), "steps": [], "edges": []})
    broken = json.dumps(graph_dict(broken=True))
    not_json = "sorry, I cannot help"
    llm = ScriptedLLM([not_json, no_steps, broken])
    result = extract_process("...", llm, max_attempts=3)

    assert not result.ok
    assert len(result.attempts) == 3
    assert len({tuple(a.errors) for a in result.attempts}) == 3


def test_junk_that_is_not_json_is_reported_not_crashed():
    llm = ScriptedLLM(["I'm sorry, I can't help with that.", json.dumps(graph_dict())])
    result = extract_process("...", llm)

    assert result.ok
    assert not result.attempts[0].ok


def test_markdown_fences_do_not_break_parsing():
    fenced = "```json\n" + json.dumps(graph_dict()) + "\n```"
    llm = ScriptedLLM([fenced])
    result = extract_process("...", llm)

    assert result.ok
    assert len(result.attempts) == 1


def test_a_recorder_that_cannot_write_still_returns_the_answer(tmp_path, monkeypatch):
    """A read-only disk must not turn a working request into a 500.

    Every serverless host has one, and this raised in the constructor before the
    model was ever reached. Recording is a convenience, and a convenience does
    not get to fail the thing it assists.
    """

    from pathlib import Path

    from app.llm.record import RecordingLLM

    def refuse(*args, **kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(Path, "mkdir", refuse)

    recorder = RecordingLLM(ScriptedLLM(["{}"]), directory=tmp_path / "nowhere")

    assert recorder.recording is False
    assert recorder.generate_json(system="s", prompt="p", schema={}) == "{}"
