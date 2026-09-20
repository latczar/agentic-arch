"""Tests for the structural rules on a process graph.

The graph validator is the contract we hold a language model to, so these tests
are really tests of that contract. Each one encodes a way a model gets it wrong
in practice.
"""

import pytest
from pydantic import ValidationError

from app.schemas.common import System, SystemCategory
from app.schemas.process import (
    Edge,
    ProcessGraph,
    Step,
    StepKind,
    Trigger,
    TriggerKind,
    validate_graph,
)


def step(sid: str, kind: StepKind = StepKind.READ, **kw) -> Step:
    return Step(id=sid, name=sid.replace("_", " ").title(), description="...", kind=kind, **kw)


def build(steps: list[Step], edges: list[Edge], first: str = "find_invoice", **kw) -> ProcessGraph:
    """Build a graph WITHOUT running the graph-level validator.

    model_construct skips validators, which lets us call validate_graph directly
    and inspect every error rather than only the first exception.
    """

    return ProcessGraph.model_construct(
        title="Invoice intake",
        summary="Invoices arrive by email and end up in a spreadsheet.",
        trigger=Trigger(
            kind=TriggerKind.SCHEDULE,
            description="Every weekday morning",
            schedule_hint="weekdays at 9am",
            first_step_id=first,
        ),
        systems=kw.pop("systems", []),
        steps=steps,
        edges=edges,
        questions=kw.pop("questions", []),
    )


def straight_line() -> tuple[list[Step], list[Edge]]:
    steps = [
        step("find_invoice"),
        step("read_amount", StepKind.EXTRACT),
        step("update_sheet", StepKind.WRITE),
    ]
    edges = [
        Edge(from_step="find_invoice", to_step="read_amount"),
        Edge(from_step="read_amount", to_step="update_sheet"),
    ]
    return steps, edges


def test_a_simple_valid_process_passes():
    steps, edges = straight_line()
    assert validate_graph(build(steps, edges)) == []


def test_the_real_constructor_rejects_a_broken_graph():
    with pytest.raises(ValidationError, match="not a step"):
        ProcessGraph(
            title="Broken",
            summary="Points at a step that does not exist.",
            trigger=Trigger(
                kind=TriggerKind.MANUAL, description="by hand", first_step_id="find_invoice"
            ),
            steps=[step("find_invoice")],
            edges=[Edge(from_step="find_invoice", to_step="nowhere")],
        )


# --- Branching: the rules that stop a model flattening a decision -------------


def test_a_decision_with_only_one_branch_is_rejected():
    steps = [step("find_invoice"), step("over_threshold", StepKind.DECISION), step("update_sheet", StepKind.WRITE)]
    edges = [
        Edge(from_step="find_invoice", to_step="over_threshold"),
        Edge(from_step="over_threshold", to_step="update_sheet", condition="always"),
    ]
    errors = validate_graph(build(steps, edges))
    assert any("needs at least two" in e for e in errors)


def test_a_decision_branch_without_a_condition_is_rejected():
    steps = [
        step("find_invoice"),
        step("over_threshold", StepKind.DECISION),
        step("get_approval", StepKind.JUDGEMENT),
        step("update_sheet", StepKind.WRITE),
    ]
    edges = [
        Edge(from_step="find_invoice", to_step="over_threshold"),
        Edge(from_step="over_threshold", to_step="get_approval", condition="amount is over 5000"),
        Edge(from_step="over_threshold", to_step="update_sheet"),  # unlabelled
    ]
    errors = validate_graph(build(steps, edges))
    assert any("no condition" in e for e in errors)


def test_only_a_decision_step_may_branch():
    steps = [step("find_invoice"), step("update_sheet", StepKind.WRITE), step("tell_accounts", StepKind.NOTIFY)]
    edges = [
        Edge(from_step="find_invoice", to_step="update_sheet"),
        Edge(from_step="find_invoice", to_step="tell_accounts"),
    ]
    errors = validate_graph(build(steps, edges))
    assert any("Only a decision step may branch" in e for e in errors)


# --- Shape: things that break layout and export -------------------------------


def test_a_loop_is_rejected_and_names_the_path():
    steps = [step("find_invoice"), step("read_amount", StepKind.EXTRACT), step("update_sheet", StepKind.WRITE)]
    edges = [
        Edge(from_step="find_invoice", to_step="read_amount"),
        Edge(from_step="read_amount", to_step="update_sheet"),
        Edge(from_step="update_sheet", to_step="read_amount"),
    ]
    errors = validate_graph(build(steps, edges))
    assert any("loops back on itself" in e for e in errors)
    assert any("read_amount -> update_sheet -> read_amount" in e for e in errors)


def test_an_orphaned_step_is_rejected():
    steps, edges = straight_line()
    steps.append(step("file_the_paperwork", StepKind.WRITE))
    errors = validate_graph(build(steps, edges))
    assert any("cannot be reached" in e for e in errors)


def test_a_process_that_never_ends_is_rejected():
    steps = [step("a"), step("b")]
    edges = [Edge(from_step="a", to_step="b"), Edge(from_step="b", to_step="a")]
    errors = validate_graph(build(steps, edges, first="a"))
    # A two-node loop trips the cycle check first, which is the more useful message.
    assert any("loops back on itself" in e for e in errors)


def test_a_step_cannot_link_to_itself():
    steps, edges = straight_line()
    edges.append(Edge(from_step="update_sheet", to_step="update_sheet"))
    errors = validate_graph(build(steps, edges))
    assert any("links to itself" in e for e in errors)


# --- References ---------------------------------------------------------------


def test_a_step_in_an_undeclared_system_is_rejected():
    steps, edges = straight_line()
    steps[0] = step("find_invoice", system_id="gmail")
    errors = validate_graph(build(steps, edges))
    assert any("not declared" in e for e in errors)


def test_a_declared_system_is_accepted():
    steps, edges = straight_line()
    steps[0] = step("find_invoice", system_id="gmail")
    systems = [System(id="gmail", name="Gmail", category=SystemCategory.EMAIL)]
    assert validate_graph(build(steps, edges, systems=systems)) == []


# --- The property the repair loop depends on ----------------------------------


def test_every_problem_is_reported_at_once():
    """A repair prompt that only ever sees one fault at a time oscillates."""

    steps = [
        step("find_invoice", system_id="gmail"),          # undeclared system
        step("over_threshold", StepKind.DECISION),        # only one branch
        step("update_sheet", StepKind.WRITE),
        step("file_the_paperwork", StepKind.WRITE),       # unreachable
    ]
    edges = [
        Edge(from_step="find_invoice", to_step="over_threshold"),
        Edge(from_step="over_threshold", to_step="update_sheet", condition="always"),
    ]
    errors = validate_graph(build(steps, edges))
    assert len(errors) >= 3
    assert any("not declared" in e for e in errors)
    assert any("needs at least two" in e for e in errors)
    assert any("cannot be reached" in e for e in errors)
