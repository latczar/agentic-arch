"""What the code changed about the model's answer, and whether it owns up to it.

The three normalise passes correct the model rather than rejecting it, which is
the right call and leaves an obvious hole: a corrected answer and a good answer
look identical by the time anybody reads one. These check the correction is
recorded, and that the record belongs to us.
"""

import json

from app.assess import assess_process
from app.schemas.assessment import OverrideKind, Verdict
from app.schemas.process import Edge, ProcessGraph, Step, StepKind, Trigger, TriggerKind
from tests.test_assess import graph, plan_dict
from tests.test_extract import ScriptedLLM


def run(plan: dict, on: ProcessGraph | None = None):
    result = assess_process(on or graph(), ScriptedLLM([json.dumps(plan)]))
    assert result.ok, result.attempts[-1].errors
    return result.plan


def tidy_up_graph() -> ProcessGraph:
    """A process ending in a step that destroys something."""

    return ProcessGraph(
        title="Inbox tidying",
        summary="Invoices are filed and the email is binned.",
        trigger=Trigger(
            kind=TriggerKind.SCHEDULE, description="Every Friday", first_step_id="file_it"
        ),
        steps=[
            Step(id="file_it", name="File it", description="Save the PDF.", kind=StepKind.WRITE),
            Step(
                id="bin_it",
                name="Delete the email",
                description="Remove it from the inbox once filed.",
                kind=StepKind.WRITE,
            ),
        ],
        edges=[Edge(from_step="file_it", to_step="bin_it")],
    )


def tidy_up_plan() -> dict:
    """Both steps waved through, which is what a model actually does here."""

    return {
        "process_title": "Inbox tidying",
        "headline": "All of this can run itself.",
        "biggest_win": "file_it",
        "assessments": [
            {
                "step_id": step,
                "verdict": "fully_automatable",
                "rationale": "Nothing to it.",
                "confidence": "high",
                "risks": [],
                "controls": [],
                "blockers": [],
            }
            for step in ("file_it", "bin_it")
        ],
    }


# --- Nothing to report -------------------------------------------------------


def test_an_answer_we_did_not_touch_carries_no_overrides():
    """Otherwise the badge appears on every step and stops meaning anything."""

    plan = run(plan_dict())
    assert plan.for_step("find_invoice").overrides == []
    assert plan.for_step("pay_invoice").overrides == []


# --- The three rules ---------------------------------------------------------


def test_a_downgraded_verdict_says_what_it_used_to_be():
    pay = run(plan_dict(reckless=True)).for_step("pay_invoice")

    downgrade = next(o for o in pay.overrides if o.kind is OverrideKind.VERDICT_DOWNGRADED)
    assert downgrade.was == "fully_automatable"
    assert downgrade.now == "automatable_with_control"
    assert "moves money" in downgrade.because


def test_a_guard_we_supplied_is_recorded_as_ours():
    pay = run(plan_dict(reckless=True)).for_step("pay_invoice")

    added = next(o for o in pay.overrides if o.kind is OverrideKind.CONTROL_ADDED)
    assert added.was == "no guard"
    assert added.now == "human_approval"


def test_a_risk_the_model_missed_is_recorded_against_the_step():
    bin_it = run(tidy_up_plan(), on=tidy_up_graph()).for_step("bin_it")

    spotted = next(o for o in bin_it.overrides if o.kind is OverrideKind.RISK_ADDED)
    assert spotted.was == "nothing flagged"
    assert spotted.now == "irreversible"
    assert "cannot be undone" in spotted.because


def test_all_three_rules_fire_in_order_on_one_step():
    """Deleting an email, waved through: missed risk, wrong verdict, no guard."""

    bin_it = run(tidy_up_plan(), on=tidy_up_graph()).for_step("bin_it")

    assert [o.kind for o in bin_it.overrides] == [
        OverrideKind.RISK_ADDED,
        OverrideKind.VERDICT_DOWNGRADED,
        OverrideKind.CONTROL_ADDED,
    ]
    assert bin_it.verdict is Verdict.AUTOMATABLE_WITH_CONTROL


def test_a_step_the_model_got_right_is_left_alone_in_the_same_run():
    """The step beside it was fine, so a blanket pass would be indistinguishable."""

    plan = run(tidy_up_plan(), on=tidy_up_graph())
    assert plan.for_step("file_it").overrides == []


# --- Who owns the record -----------------------------------------------------


def test_overrides_the_model_invents_are_thrown_away():
    """The whole point is that this is our account of correcting it, not its own.

    A model that can write here can write "nothing was changed" over the top of
    a correction, and the one place the reader looks to see whether the safety
    net fired becomes the least trustworthy thing on the page.
    """

    plan = tidy_up_plan()
    plan["assessments"][1]["overrides"] = [
        {
            "kind": "risk_added",
            "was": "everything was fine",
            "now": "everything is still fine",
            "because": "No correction was needed here.",
        }
    ]

    bin_it = run(plan, on=tidy_up_graph()).for_step("bin_it")

    assert all("fine" not in o.was for o in bin_it.overrides)
    assert all("No correction" not in o.because for o in bin_it.overrides)
    assert any(o.kind is OverrideKind.VERDICT_DOWNGRADED for o in bin_it.overrides)


def test_every_override_names_both_sides_of_the_change():
    """A record saying only what it is now does not show anybody a disagreement."""

    for assessment in run(tidy_up_plan(), on=tidy_up_graph()).assessments:
        for override in assessment.overrides:
            assert override.was and override.now
            assert override.was != override.now
            assert override.because.endswith(".")


def test_the_reason_is_readable_rather_than_our_enum():
    """It is shown to somebody who has never seen this codebase."""

    bin_it = run(tidy_up_plan(), on=tidy_up_graph()).for_step("bin_it")

    for override in bin_it.overrides:
        assert "_" not in override.because


# --- Judgement, since it joined the never-unattended list --------------------


def test_a_judgement_waved_through_is_downgraded_not_rejected():
    """Adding judgement to the list must correct answers, never invalidate them.

    The schema refuses fully_automatable on any step carrying a never-unattended
    risk. That is only safe because the corrections run before validation, so
    this pins the order: a model that marks a judgement step as running itself
    gets a valid plan back, downgraded, with the change on record.
    """

    graph_ = ProcessGraph(
        title="Deposit return",
        summary="Deductions are decided and written up.",
        trigger=Trigger(kind=TriggerKind.MANUAL, description="A tenancy ends", first_step_id="decide"),
        steps=[
            Step(id="decide", name="Decide the deductions", description="Work out what to keep back.",
                 kind=StepKind.JUDGEMENT),
            Step(id="file", name="File the reports", description="Save both reports.",
                 kind=StepKind.WRITE),
        ],
        edges=[Edge(from_step="decide", to_step="file")],
    )
    plan = tidy_up_plan()
    plan["biggest_win"] = "file"
    plan["assessments"] = [
        {**plan["assessments"][0], "step_id": step} for step in ("decide", "file")
    ]
    plan["assessments"][0]["risks"] = ["subjective_judgement"]

    decided = run(plan, on=graph_).for_step("decide")

    assert decided.verdict is Verdict.AUTOMATABLE_WITH_CONTROL
    kinds = [o.kind for o in decided.overrides]
    assert OverrideKind.VERDICT_DOWNGRADED in kinds
    assert decided.controls, "a downgraded step must be given a guard"
