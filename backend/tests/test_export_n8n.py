"""Tests for the n8n export.

The point of these is that the file imports and has the right shape. We cannot
assert that it runs, because it deliberately does not: the integration nodes are
placeholders. What we can assert is that the structure survives the trip.
"""

import json

from app.export_n8n import to_n8n, to_n8n_json
from app.schemas.assessment import (
    AutomationPlan,
    Confidence,
    Control,
    ControlKind,
    RiskFlag,
    StepAssessment,
    Verdict,
)
from app.schemas.common import ComparisonOperator, DataType, Threshold
from app.schemas.process import Edge, ProcessGraph, Step, StepKind, Trigger, TriggerKind


def branching_graph() -> ProcessGraph:
    return ProcessGraph(
        title="Invoice intake",
        summary="Invoices arrive by email.",
        trigger=Trigger(
            kind=TriggerKind.SCHEDULE,
            description="Every weekday morning",
            first_step_id="find_invoice",
        ),
        steps=[
            Step(id="find_invoice", name="Find the invoice", description="...", kind=StepKind.READ),
            Step(id="over_limit", name="Is it a big one?", description="...", kind=StepKind.DECISION),
            Step(id="ask_manager", name="Ask the manager", description="...", kind=StepKind.JUDGEMENT),
            Step(id="pay_it", name="Pay the invoice", description="...", kind=StepKind.WRITE),
        ],
        edges=[
            Edge(from_step="find_invoice", to_step="over_limit"),
            Edge(from_step="over_limit", to_step="ask_manager", condition="over 5000"),
            Edge(from_step="over_limit", to_step="pay_it", condition="5000 or under"),
            Edge(from_step="ask_manager", to_step="pay_it"),
        ],
    )


def plan_with(controls: list[Control]) -> AutomationPlan:
    return AutomationPlan(
        process_title="Invoice intake",
        headline="Mostly automatable.",
        assessments=[
            StepAssessment(
                step_id="find_invoice", verdict=Verdict.FULLY_AUTOMATABLE,
                rationale="Safe.", confidence=Confidence.HIGH,
            ),
            StepAssessment(
                step_id="over_limit", verdict=Verdict.FULLY_AUTOMATABLE,
                rationale="Arithmetic.", confidence=Confidence.HIGH,
            ),
            StepAssessment(
                step_id="ask_manager", verdict=Verdict.HUMAN_REQUIRED,
                rationale="A person signs off.", confidence=Confidence.HIGH,
            ),
            # Without a control the schema will not allow the guarded verdict,
            # which is the point of it. Tests about node shape rather than
            # controls use the plain verdict instead.
            StepAssessment(
                step_id="pay_it",
                verdict=Verdict.AUTOMATABLE_WITH_CONTROL if controls else Verdict.FULLY_AUTOMATABLE,
                rationale="Money leaves the account.", confidence=Confidence.HIGH,
                risks=[RiskFlag.MOVES_MONEY] if controls else [], controls=controls,
            ),
        ],
    )


def approval() -> Control:
    return Control(kind=ControlKind.HUMAN_APPROVAL, reason="Ask first.",
                   who_approves="whoever owns the ledger")


def threshold_approval() -> Control:
    return Control(
        kind=ControlKind.THRESHOLD_APPROVAL,
        reason="Big ones wait for a person.",
        threshold=Threshold(
            field="invoice.total", operator=ComparisonOperator.GT,
            value="5000", value_type=DataType.MONEY, currency="GBP",
        ),
    )


def names(workflow: dict) -> list[str]:
    return [n["name"] for n in workflow["nodes"]]


def types(workflow: dict) -> list[str]:
    return [n["type"] for n in workflow["nodes"]]


# --- Shape --------------------------------------------------------------------


def test_it_produces_a_trigger_and_one_node_per_step():
    workflow = to_n8n(branching_graph(), plan_with([]))
    assert any("Trigger" in t or "trigger" in t for t in types(workflow))
    for label in ("Find the invoice", "Is it a big one?", "Ask the manager", "Pay the invoice"):
        assert label in names(workflow)


def test_a_decision_becomes_a_real_if_node_with_two_outputs():
    workflow = to_n8n(branching_graph(), plan_with([]))

    decision = next(n for n in workflow["nodes"] if n["name"] == "Is it a big one?")
    assert decision["type"] == "n8n-nodes-base.if"

    outputs = workflow["connections"]["Is it a big one?"]["main"]
    assert len([o for o in outputs if o]) == 2
    assert outputs[0][0]["node"] == "Ask the manager"
    assert outputs[1][0]["node"] == "Pay the invoice"


def test_every_connection_points_at_a_node_that_exists():
    """An import fails outright on a dangling connection, so this one matters."""

    workflow = to_n8n(branching_graph(), plan_with([threshold_approval()]))
    declared = set(names(workflow))

    for source, connection in workflow["connections"].items():
        assert source in declared, f"connection from unknown node {source}"
        for output in connection["main"]:
            for target in output:
                assert target["node"] in declared, f"dangling edge to {target['node']}"


def test_node_names_are_unique():
    """n8n keys connections by name, so a duplicate silently rewires the graph."""

    workflow = to_n8n(branching_graph(), plan_with([approval()]))
    assert len(names(workflow)) == len(set(names(workflow)))


# --- Controls -----------------------------------------------------------------


def test_an_approval_becomes_a_wait_node_in_front_of_the_step():
    workflow = to_n8n(branching_graph(), plan_with([approval()]))

    wait = next(n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.wait")
    assert "Pay the invoice" in wait["name"]
    assert "whoever owns the ledger" in wait["notes"]

    # Whatever fed the step now feeds the wait instead.
    assert workflow["connections"][wait["name"]]["main"][0][0]["node"] == "Pay the invoice"


def test_a_threshold_approval_becomes_a_branch_on_the_limit():
    """This is what the structured threshold on a control was for."""

    workflow = to_n8n(branching_graph(), plan_with([threshold_approval()]))

    gate = next(
        n for n in workflow["nodes"]
        if n["type"] == "n8n-nodes-base.if" and "limit" in n["name"].lower()
    )
    condition = gate["parameters"]["conditions"]["conditions"][0]
    assert "invoice.total" in condition["leftValue"]
    assert condition["rightValue"] == 5000.0
    assert condition["operator"]["operation"] == "gt"

    # Above the limit waits; below it goes straight through.
    outputs = workflow["connections"][gate["name"]]["main"]
    assert "Wait for approval" in outputs[0][0]["node"]
    assert outputs[1][0]["node"] == "Pay the invoice"


def test_placeholders_say_what_to_replace_them_with():
    workflow = to_n8n(branching_graph(), plan_with([]))
    placeholder = next(n for n in workflow["nodes"] if n["name"] == "Find the invoice")
    assert "REPLACE THIS" in placeholder["notes"]


def test_it_works_without_a_plan_at_all():
    workflow = to_n8n(branching_graph(), None)
    assert names(workflow)
    assert not any(n["type"] == "n8n-nodes-base.wait" for n in workflow["nodes"])


def test_the_json_round_trips():
    parsed = json.loads(to_n8n_json(branching_graph(), plan_with([approval()])))
    assert parsed["name"] == "Invoice intake"
    assert parsed["nodes"] and parsed["connections"]
