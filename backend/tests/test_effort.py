"""Tests for the time arithmetic.

Boring on purpose. This is the one part of the output a reader can check against
their own experience, so it has to be right, and it has to be theirs.
"""

import pytest
from pydantic import ValidationError

from app.schemas.assessment import AutomationPlan, Confidence, StepAssessment, Verdict
from app.schemas.effort import EffortInput, Period, summarise_effort


def plan() -> AutomationPlan:
    def assess(step_id: str, verdict: Verdict) -> StepAssessment:
        controls = []
        if verdict is Verdict.AUTOMATABLE_WITH_CONTROL:
            from app.schemas.assessment import Control, ControlKind

            controls = [Control(kind=ControlKind.HUMAN_APPROVAL, reason="Ask first.")]
        return StepAssessment(
            step_id=step_id, verdict=verdict, rationale="...",
            confidence=Confidence.HIGH, controls=controls,
        )

    return AutomationPlan(
        process_title="Invoice intake",
        headline="Mostly automatable.",
        assessments=[
            assess("find_invoice", Verdict.FULLY_AUTOMATABLE),
            assess("read_total", Verdict.FULLY_AUTOMATABLE),
            assess("approve_it", Verdict.AUTOMATABLE_WITH_CONTROL),
            assess("sense_check", Verdict.HUMAN_REQUIRED),
        ],
    )


def effort(**kw) -> EffortInput:
    return EffortInput(
        times_per_period=kw.pop("times", 10),
        period=kw.pop("period", Period.WEEK),
        minutes_per_step=kw.pop(
            "minutes",
            {"find_invoice": 2, "read_total": 3, "approve_it": 1, "sense_check": 4},
        ),
    )


def test_a_weekly_process_is_converted_to_a_monthly_figure():
    summary = summarise_effort(effort(times=10, period=Period.WEEK), plan())

    # 10 a week is about 43.5 a month, ten minutes each, so roughly 7.2 hours.
    assert summary.runs_per_month == pytest.approx(43.5, abs=0.2)
    assert summary.hours_per_month == pytest.approx(7.2, abs=0.2)


def test_only_fully_automatable_steps_count_as_saved():
    summary = summarise_effort(effort(), plan())

    # find_invoice and read_total are 5 of the 10 minutes.
    assert summary.hours_that_could_run_without_you == pytest.approx(
        summary.hours_per_month / 2, abs=0.2
    )
    assert summary.percentage_automatable == 50


def test_a_guarded_step_counts_as_still_needing_you():
    """Understating the saving is the honest direction to be wrong in."""

    summary = summarise_effort(effort(), plan())
    guarded = next(s for s in summary.steps if s.step_id == "approve_it")
    assert not guarded.could_run_without_you


def test_the_halves_add_back_up_to_the_whole():
    summary = summarise_effort(effort(), plan())
    assert summary.hours_that_could_run_without_you + summary.hours_that_still_need_you == (
        pytest.approx(summary.hours_per_month, abs=0.1)
    )


def test_a_monthly_process_runs_once_a_month():
    summary = summarise_effort(effort(times=1, period=Period.MONTH), plan())
    assert summary.runs_per_month == 1.0
    assert summary.hours_per_month == pytest.approx(10 / 60, abs=0.01)  # not rounded to 0.2


def test_working_days_are_not_calendar_days():
    working = summarise_effort(effort(times=1, period=Period.WORKING_DAY), plan())
    calendar = summarise_effort(effort(times=1, period=Period.DAY), plan())
    assert working.runs_per_month < calendar.runs_per_month


def test_a_step_with_no_duration_given_contributes_nothing():
    summary = summarise_effort(effort(minutes={"find_invoice": 5}), plan())
    assert summary.hours_per_month == pytest.approx(5 * summary.runs_per_month / 60, abs=0.1)
    assert all(
        s.minutes_each_time == 0 for s in summary.steps if s.step_id != "find_invoice"
    )


def test_nothing_is_automatable_when_nothing_takes_any_time():
    summary = summarise_effort(effort(minutes={}), plan())
    assert summary.hours_per_month == 0
    assert summary.percentage_automatable == 0


def test_the_caveat_is_always_attached():
    """The figure is meaningless without the sentence explaining what it excludes."""

    summary = summarise_effort(effort(), plan())
    assert "still needing you" in summary.caveat or "still need" in summary.caveat


# --- Input sanity -------------------------------------------------------------


def test_a_frequency_of_zero_is_rejected():
    with pytest.raises(ValidationError):
        EffortInput(times_per_period=0, period=Period.WEEK)


def test_a_negative_duration_is_rejected():
    with pytest.raises(ValidationError, match="negative"):
        EffortInput(times_per_period=1, period=Period.WEEK, minutes_per_step={"a": -1})


def test_an_implausibly_long_step_is_rejected():
    with pytest.raises(ValidationError, match="eight hours"):
        EffortInput(times_per_period=1, period=Period.WEEK, minutes_per_step={"a": 600})


def test_small_totals_keep_enough_precision_to_be_useful():
    """Rounding to one decimal place turned ten minutes a month into 0.2 hours."""

    summary = summarise_effort(
        EffortInput(times_per_period=1, period=Period.MONTH, minutes_per_step={"find_invoice": 6}),
        plan(),
    )
    assert summary.hours_per_month == 0.1
