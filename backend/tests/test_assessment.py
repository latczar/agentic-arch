"""Tests for the judgement layer.

Each test is a way the output could look plausible while being wrong in a way
that would embarrass us in front of a client.
"""

import pytest
from pydantic import ValidationError

from app.schemas.common import ComparisonOperator, DataType, Threshold
from app.schemas.assessment import (
    AutomationPlan,
    Blocker,
    BlockerKind,
    Confidence,
    Control,
    ControlKind,
    RiskFlag,
    StepAssessment,
    Verdict,
    validate_plan,
)
from app.schemas.process import Edge, ProcessGraph, Step, StepKind, Trigger, TriggerKind


def assess(step_id: str, verdict: Verdict, **kw) -> StepAssessment:
    return StepAssessment(
        step_id=step_id,
        verdict=verdict,
        rationale="...",
        confidence=kw.pop("confidence", Confidence.MEDIUM),
        **kw,
    )


def money_threshold(amount: str = "5000") -> Threshold:
    return Threshold(
        field="invoice.total",
        operator=ComparisonOperator.GT,
        value=amount,
        value_type=DataType.MONEY,
        currency="GBP",
    )


# --- The house rule -----------------------------------------------------------


def test_a_step_that_moves_money_cannot_be_fully_automatic():
    with pytest.raises(ValidationError, match="moves_money"):
        assess("pay_invoice", Verdict.FULLY_AUTOMATABLE, risks=[RiskFlag.MOVES_MONEY])


def test_an_irreversible_step_cannot_be_fully_automatic():
    with pytest.raises(ValidationError, match="irreversible"):
        assess("delete_records", Verdict.FULLY_AUTOMATABLE, risks=[RiskFlag.IRREVERSIBLE])


def test_the_same_step_is_fine_with_a_control():
    a = assess(
        "pay_invoice",
        Verdict.AUTOMATABLE_WITH_CONTROL,
        risks=[RiskFlag.MOVES_MONEY],
        controls=[
            Control(
                kind=ControlKind.THRESHOLD_APPROVAL,
                reason="Large payments get a second pair of eyes.",
                addresses=[RiskFlag.MOVES_MONEY],
                threshold=money_threshold(),
                who_approves="whoever owns the purchase ledger",
            )
        ],
    )
    assert a.needs_a_person


def test_lower_risk_work_can_still_be_fully_automatic():
    a = assess("update_sheet", Verdict.FULLY_AUTOMATABLE, risks=[RiskFlag.PERSONAL_DATA])
    assert not a.needs_a_person


# --- Internal contradictions --------------------------------------------------


def test_a_controlled_verdict_must_actually_name_a_control():
    with pytest.raises(ValidationError, match=r"lists no\s+control"):
        assess("send_email", Verdict.AUTOMATABLE_WITH_CONTROL, risks=[RiskFlag.EXTERNAL_COMMS])


def test_fully_automatable_cannot_also_list_blockers():
    with pytest.raises(ValidationError, match="blocker"):
        assess(
            "read_post",
            Verdict.FULLY_AUTOMATABLE,
            blockers=[Blocker(kind=BlockerKind.PAPER_OR_PHYSICAL, detail="It arrives in the post.")],
        )


def test_needs_more_info_must_say_what_is_missing():
    with pytest.raises(ValidationError, match="does not say what is missing"):
        assess("mystery_step", Verdict.NEEDS_MORE_INFO)


def test_threshold_approval_without_a_limit_is_rejected():
    with pytest.raises(ValidationError, match="needs a threshold"):
        Control(kind=ControlKind.THRESHOLD_APPROVAL, reason="Big ones need checking.")


def test_a_plain_approval_should_not_carry_a_threshold():
    with pytest.raises(ValidationError, match="should not carry a threshold"):
        Control(
            kind=ControlKind.HUMAN_APPROVAL,
            reason="Always ask.",
            threshold=money_threshold(),
        )


# --- Plan against graph -------------------------------------------------------


def invoice_graph() -> ProcessGraph:
    return ProcessGraph(
        title="Invoice intake",
        summary="Invoices arrive by email and end up in a spreadsheet.",
        trigger=Trigger(
            kind=TriggerKind.SCHEDULE,
            description="Every weekday morning",
            first_step_id="find_invoice",
        ),
        steps=[
            Step(id="find_invoice", name="Find the invoice", description="...", kind=StepKind.READ),
            Step(id="sense_check", name="Sense check it", description="...", kind=StepKind.JUDGEMENT),
            Step(id="update_sheet", name="Update the sheet", description="...", kind=StepKind.WRITE),
        ],
        edges=[
            Edge(from_step="find_invoice", to_step="sense_check"),
            Edge(from_step="sense_check", to_step="update_sheet"),
        ],
    )


def plan_with(assessments: list[StepAssessment], **kw) -> AutomationPlan:
    return AutomationPlan(
        process_title="Invoice intake",
        assessments=assessments,
        headline="Most of this can run itself; the sense check stays with you.",
        **kw,
    )


def test_a_complete_plan_passes():
    plan = plan_with(
        [
            assess("find_invoice", Verdict.FULLY_AUTOMATABLE),
            assess("sense_check", Verdict.HUMAN_REQUIRED, risks=[RiskFlag.SUBJECTIVE_JUDGEMENT]),
            assess("update_sheet", Verdict.FULLY_AUTOMATABLE),
        ]
    )
    assert validate_plan(plan, invoice_graph()) == []


def test_a_missed_step_is_caught():
    plan = plan_with([assess("find_invoice", Verdict.FULLY_AUTOMATABLE)])
    errors = validate_plan(plan, invoice_graph())
    assert any("'sense_check' has no assessment" in e for e in errors)
    assert any("'update_sheet' has no assessment" in e for e in errors)


def test_an_invented_step_is_caught():
    plan = plan_with(
        [
            assess("find_invoice", Verdict.FULLY_AUTOMATABLE),
            assess("sense_check", Verdict.HUMAN_REQUIRED),
            assess("update_sheet", Verdict.FULLY_AUTOMATABLE),
            assess("post_to_xero", Verdict.FULLY_AUTOMATABLE),
        ]
    )
    errors = validate_plan(plan, invoice_graph())
    assert any("not in the process" in e for e in errors)


def test_human_judgement_cannot_be_quietly_automated_away():
    plan = plan_with(
        [
            assess("find_invoice", Verdict.FULLY_AUTOMATABLE),
            assess("sense_check", Verdict.FULLY_AUTOMATABLE),
            assess("update_sheet", Verdict.FULLY_AUTOMATABLE),
        ]
    )
    errors = validate_plan(plan, invoice_graph())
    assert any("described as human judgement" in e for e in errors)


def test_counts_summarise_the_plan():
    plan = plan_with(
        [
            assess("find_invoice", Verdict.FULLY_AUTOMATABLE),
            assess("sense_check", Verdict.HUMAN_REQUIRED),
            assess("update_sheet", Verdict.FULLY_AUTOMATABLE),
        ]
    )
    counts = plan.counts()
    assert counts[Verdict.FULLY_AUTOMATABLE] == 2
    assert counts[Verdict.HUMAN_REQUIRED] == 1
