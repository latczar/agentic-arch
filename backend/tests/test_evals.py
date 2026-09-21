"""Tests for the scoring harness.

An eval suite is a measuring instrument, and an instrument nobody has calibrated
is just a confident opinion with a number attached. If a check cannot fail, it
is decoration, so most of what follows is proving the checks reject things as
well as accepting them.
"""

import json

import pytest

from app.schemas.assessment import (
    AutomationPlan,
    Confidence,
    Control,
    ControlKind,
    RiskFlag,
    StepAssessment,
    Verdict,
)
from app.schemas.process import Edge, ProcessGraph, Step, StepKind, Trigger, TriggerKind
from evals import cases as case_module
from evals.checks import (
    every_step_assessed,
    flagged,
    has_a_decision,
    never_unattended,
    something_is_automatable,
    steps_between,
)
from evals.runner import (
    CaseResult,
    Report,
    grade_guard,
    load_baseline,
    run_guards,
    save_baseline,
)


def graph_with(*steps: Step) -> ProcessGraph:
    edges = [
        Edge(from_step=a.id, to_step=b.id) for a, b in zip(steps, steps[1:])
    ]
    return ProcessGraph(
        title="A process",
        summary="Something somebody does by hand.",
        trigger=Trigger(
            kind=TriggerKind.SCHEDULE,
            description="every morning",
            first_step_id=steps[0].id,
        ),
        steps=list(steps),
        edges=edges,
    )


def step(step_id: str, name: str, kind: StepKind = StepKind.WRITE) -> Step:
    return Step(id=step_id, name=name, description=name, kind=kind)


def plan_for(graph: ProcessGraph, **verdicts: Verdict) -> AutomationPlan:
    assessments = []
    for s in graph.steps:
        verdict = verdicts.get(s.id, Verdict.HUMAN_REQUIRED)
        controls = []
        if verdict is Verdict.AUTOMATABLE_WITH_CONTROL:
            controls = [
                Control(kind=ControlKind.HUMAN_APPROVAL, reason="Somebody should look.")
            ]
        assessments.append(
            StepAssessment(
                step_id=s.id,
                verdict=verdict,
                rationale="...",
                confidence=Confidence.MEDIUM,
                controls=controls,
            )
        )
    return AutomationPlan(
        process_title=graph.title, assessments=assessments, headline="..."
    )


# --- The checks reject as well as accept --------------------------------------


def test_never_unattended_fails_when_a_dangerous_step_is_waved_through():
    graph = graph_with(step("a", "Read the inbox"), step("b", "Delete the email"))
    plan = plan_for(graph, b=Verdict.FULLY_AUTOMATABLE)

    outcome = never_unattended("delete").run(graph, plan)

    assert not outcome.passed
    assert "Delete the email" in outcome.detail


def test_never_unattended_passes_when_the_same_step_is_guarded():
    graph = graph_with(step("a", "Read the inbox"), step("b", "Delete the email"))
    plan = plan_for(graph, b=Verdict.AUTOMATABLE_WITH_CONTROL)

    assert never_unattended("delete").run(graph, plan).passed


def test_a_check_that_matches_nothing_says_so_rather_than_failing_silently():
    """A check with a typo in it should look different from a check that passed."""

    graph = graph_with(step("a", "Read the inbox"))
    outcome = never_unattended("xyzzy").run(graph, plan_for(graph))

    assert outcome.passed
    assert "nothing to judge" in outcome.detail


def test_flagged_fails_when_the_risk_is_missing():
    graph = graph_with(step("a", "Pay the supplier"))
    outcome = flagged(RiskFlag.MOVES_MONEY, "pay").run(graph, plan_for(graph))

    assert not outcome.passed
    assert "moves_money" in outcome.detail


def test_steps_between_fails_both_directions():
    small = graph_with(step("a", "Do the whole job"))
    assert not steps_between(3, 8).run(small, plan_for(small)).passed

    many = graph_with(*[step(f"s{i}", f"Step {i}") for i in range(10)])
    assert not steps_between(3, 8).run(many, plan_for(many)).passed


def test_has_a_decision_fails_on_a_straight_line():
    graph = graph_with(step("a", "Read it"), step("b", "Type it in"))
    assert not has_a_decision().run(graph, plan_for(graph)).passed


def test_something_is_automatable_fails_when_everything_needs_a_person():
    graph = graph_with(step("a", "Think about it"), step("b", "Decide"))
    outcome = something_is_automatable().run(graph, plan_for(graph))

    assert not outcome.passed, "a tool that automates nothing is useless, not safe"


def test_every_step_assessed_notices_a_missing_verdict():
    graph = graph_with(step("a", "Read it"), step("b", "Type it in"))
    plan = plan_for(graph)
    plan.assessments.pop()

    outcome = every_step_assessed().run(graph, plan)

    assert not outcome.passed
    assert "b" in outcome.detail


def test_a_grader_that_throws_is_a_failed_check_not_a_failed_run():
    from evals.checks import Check

    exploding = Check("boom", lambda g, p: 1 / 0)
    graph = graph_with(step("a", "Read it"))

    outcome = exploding.run(graph, plan_for(graph))

    assert not outcome.passed
    assert "raised" in outcome.detail


# --- The guard suite ----------------------------------------------------------


def test_every_guard_case_has_a_reason_written_down():
    """A case nobody can justify is a case nobody will dare change."""

    for case in case_module.GUARD_CASES:
        assert case.why.strip(), f"{case.id} does not say why it exists"


def test_guard_case_ids_are_unique():
    ids = [c.id for c in case_module.GUARD_CASES]
    assert len(ids) == len(set(ids))


def test_the_suite_tests_both_directions():
    """Only listing dangerous steps would score a permanently-amber build at 100%."""

    assert case_module.SHOULD_FIRE
    assert case_module.SHOULD_STAY_QUIET


def test_grade_guard_reports_a_missed_risk():
    case = next(c for c in case_module.SHOULD_FIRE if c.id == "deletes-an-email")
    result = grade_guard(case)

    assert result.ok, result.outcomes[0].detail


def test_grade_guard_reports_a_spurious_flag():
    from evals.cases import GuardCase

    # Expecting nothing from a step that plainly deletes something, so the
    # grader has to call this out rather than shrug.
    case = GuardCase(
        id="made-up",
        step_name="Delete everything",
        kind="write",
        expect=frozenset(),
        why="Proving the grader objects to a flag it did not ask for.",
    )
    result = grade_guard(case)

    assert not result.ok
    assert "wrongly flagged" in result.outcomes[0].detail


def test_a_known_gap_fails_without_being_a_surprise():
    report = run_guards()

    assert report.gaps, "the suite should still be recording gaps we have not closed"
    assert not report.surprises, [
        (r.case_id, r.outcomes[0].detail) for r in report.surprises
    ]


# --- Baselines ----------------------------------------------------------------


def test_a_dropped_score_is_reported_as_a_regression(tmp_path):
    before = Report(mode="guards", results=[_result("a", 3, 3)])
    save_baseline(before, root=tmp_path)

    after = Report(mode="guards", results=[_result("a", 1, 3)])
    regressions, improvements = after.compare(load_baseline("guards", root=tmp_path))

    assert regressions and not improvements
    assert "3/3 before, 1/3 now" in regressions[0]


def test_a_case_that_vanishes_counts_as_a_regression(tmp_path):
    """Deleting the failing case is the easiest way to make a score go up."""

    save_baseline(Report(mode="guards", results=[_result("a", 1, 1)]), root=tmp_path)

    after = Report(mode="guards", results=[])
    regressions, _ = after.compare(load_baseline("guards", root=tmp_path))

    assert regressions and "did not run" in regressions[0]


def test_an_improvement_is_reported_too(tmp_path):
    save_baseline(Report(mode="guards", results=[_result("a", 1, 3)]), root=tmp_path)

    after = Report(mode="guards", results=[_result("a", 3, 3)])
    regressions, improvements = after.compare(load_baseline("guards", root=tmp_path))

    assert improvements and not regressions


def test_a_baseline_is_readable_json(tmp_path):
    path = save_baseline(Report(mode="guards", results=[_result("a", 2, 3)]), root=tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))

    assert saved["score"] == pytest.approx(2 / 3, rel=1e-3)
    assert saved["cases"]["a"] == {"passed": 2, "total": 3}


def _result(case_id: str, passed: int, total: int) -> CaseResult:
    from evals.checks import Outcome

    outcomes = [Outcome(f"check {i}", i < passed, "") for i in range(total)]
    return CaseResult(case_id=case_id, outcomes=outcomes)
