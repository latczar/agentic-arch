"""Tests for the judgement stage, again using a scripted stand-in for the model."""

import json

from app.assess import assess_process
from app.schemas.assessment import ControlKind, RiskFlag, Verdict
from app.schemas.process import Edge, ProcessGraph, Step, StepKind, Trigger, TriggerKind
from tests.test_extract import ScriptedLLM


def graph() -> ProcessGraph:
    return ProcessGraph(
        title="Invoice intake",
        summary="Invoices arrive by email and end up in a spreadsheet.",
        trigger=Trigger(
            kind=TriggerKind.SCHEDULE,
            description="Every morning",
            first_step_id="find_invoice",
        ),
        steps=[
            Step(id="find_invoice", name="Find it", description="...", kind=StepKind.READ),
            Step(id="pay_invoice", name="Pay it", description="...", kind=StepKind.WRITE),
        ],
        edges=[Edge(from_step="find_invoice", to_step="pay_invoice")],
    )


def plan_dict(*, missing_step: bool = False, reckless: bool = False) -> dict:
    assessments = [
        {
            "step_id": "find_invoice",
            "verdict": "fully_automatable",
            "rationale": "Reading the inbox is safe.",
            "confidence": "high",
            "risks": [],
            "controls": [],
            "blockers": [],
        },
        {
            "step_id": "pay_invoice",
            "verdict": "automatable_with_control",
            "rationale": "It releases money, so it needs a second pair of eyes.",
            "confidence": "high",
            "risks": ["moves_money"],
            "controls": [
                {
                    "kind": "threshold_approval",
                    "reason": "Large payments wait for a person.",
                    "addresses": ["moves_money"],
                    "threshold": {
                        "field": "invoice.total",
                        "operator": "gt",
                        "value": "5000",
                        "value_type": "money",
                        "currency": "GBP",
                    },
                    "who_approves": "whoever owns the ledger",
                }
            ],
            "blockers": [],
        },
    ]

    if reckless:
        # The classic failure: money marked as safe to run unattended.
        assessments[1] = {
            "step_id": "pay_invoice",
            "verdict": "fully_automatable",
            "rationale": "It can just pay them.",
            "confidence": "high",
            "risks": ["moves_money"],
            "controls": [],
            "blockers": [],
        }

    if missing_step:
        assessments = assessments[:1]

    return {
        "process_title": "Invoice intake",
        "assessments": assessments,
        "headline": "Most of this can run itself; payments stop for you.",
        "biggest_win": "find_invoice",
    }


def test_a_sound_plan_is_accepted_first_time():
    llm = ScriptedLLM([json.dumps(plan_dict())])
    result = assess_process(graph(), llm)

    assert result.ok
    assert len(result.attempts) == 1


def test_marking_a_payment_fully_automatic_is_corrected_not_rejected():
    """The old behaviour was to reject and retry. Correcting it is safer and cheaper."""

    llm = ScriptedLLM([json.dumps(plan_dict(reckless=True))])
    result = assess_process(graph(), llm)

    assert result.ok
    assert len(result.attempts) == 1

    pay = result.plan.for_step("pay_invoice")
    assert pay.verdict is Verdict.AUTOMATABLE_WITH_CONTROL
    assert pay.controls


def test_a_skipped_step_is_caught_by_the_cross_check():
    llm = ScriptedLLM([json.dumps(plan_dict(missing_step=True)), json.dumps(plan_dict())])
    result = assess_process(graph(), llm)

    assert result.ok
    assert any("no assessment" in e for e in result.attempts[0].errors)


def test_the_repair_prompt_shows_the_process_and_the_objections():
    """Uses a skipped step, which is a fault normalisation cannot paper over."""

    llm = ScriptedLLM([json.dumps(plan_dict(missing_step=True)), json.dumps(plan_dict())])
    assess_process(graph(), llm)

    repair = llm.prompts[1]
    assert "pay_invoice" in repair
    assert "no assessment" in repair
    assert "does not satisfy the rules" in repair


def test_it_stops_early_when_the_same_fault_comes_back():
    incomplete = json.dumps(plan_dict(missing_step=True))
    llm = ScriptedLLM([incomplete, incomplete, incomplete, incomplete])
    result = assess_process(graph(), llm, max_attempts=3)

    assert not result.ok
    assert result.plan is None
    assert len(result.attempts) == 2


def test_a_missing_guard_is_filled_in_rather_than_rejected():
    """A risky step with no control gets a conservative one, not a failed run."""

    plan = plan_dict()
    plan["assessments"][1]["controls"] = []
    llm = ScriptedLLM([json.dumps(plan)])
    result = assess_process(graph(), llm)

    assert result.ok
    assert len(result.attempts) == 1

    pay = result.plan.for_step("pay_invoice")
    assert len(pay.controls) == 1
    assert pay.controls[0].kind is ControlKind.HUMAN_APPROVAL
    assert "Added automatically" in pay.controls[0].reason


def test_a_control_the_model_chose_is_left_alone():
    llm = ScriptedLLM([json.dumps(plan_dict())])
    result = assess_process(graph(), llm)

    pay = result.plan.for_step("pay_invoice")
    assert pay.controls[0].kind is ControlKind.THRESHOLD_APPROVAL
    assert "Added automatically" not in pay.controls[0].reason


def test_an_irreversible_step_is_caught_even_when_the_model_missed_it():
    """The gap that prompted this: "delete the email" came back fully automatic."""

    g = ProcessGraph(
        title="Inbox tidying",
        summary="Read it, then bin it.",
        trigger=Trigger(kind=TriggerKind.SCHEDULE, description="Daily", first_step_id="read_it"),
        steps=[
            Step(id="read_it", name="Read the message", description="...", kind=StepKind.READ),
            Step(id="bin_it", name="Delete the email", description="Remove it from the inbox.",
                 kind=StepKind.WRITE),
        ],
        edges=[Edge(from_step="read_it", to_step="bin_it")],
    )

    oblivious = {
        "process_title": "Inbox tidying",
        "headline": "All of this can run itself.",
        "assessments": [
            {"step_id": "read_it", "verdict": "fully_automatable", "rationale": "Safe.",
             "confidence": "high", "risks": [], "controls": [], "blockers": []},
            {"step_id": "bin_it", "verdict": "fully_automatable", "rationale": "Keeps it tidy.",
             "confidence": "high", "risks": [], "controls": [], "blockers": []},
        ],
    }

    result = assess_process(g, ScriptedLLM([json.dumps(oblivious)]))

    assert result.ok
    binned = result.plan.for_step("bin_it")
    assert RiskFlag.IRREVERSIBLE in binned.risks
    assert binned.verdict is Verdict.AUTOMATABLE_WITH_CONTROL
    assert binned.controls

    # And it must not flag the harmless one.
    assert result.plan.for_step("read_it").verdict is Verdict.FULLY_AUTOMATABLE
