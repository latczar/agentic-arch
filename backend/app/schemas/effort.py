"""How much time the process takes, and how much of it could come back.

Every number here comes from the person doing the work. None of it is generated,
estimated or inferred by a model, and that is the whole point of the file.

A language model will happily tell you a process takes 11.5 hours a month and
that 71% of it is automatable. Those figures are invented. Anyone who stops to
think about where they came from discounts the entire output, including the
parts that were sound. Asking for the two facts a person actually knows, how
often they do it and roughly how long each bit takes, costs them thirty seconds
and makes the arithmetic theirs.

It is also better as a piece of design. Typing "I do this twenty times a week
and it takes six minutes" is usually the moment someone realises they have a
problem worth solving.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, computed_field, model_validator

from app.schemas.assessment import AutomationPlan, Verdict
from app.schemas.common import Base, Slug


class Period(StrEnum):
    DAY = "day"
    WORKING_DAY = "working_day"
    WEEK = "week"
    MONTH = "month"


# Averages rather than round numbers, because a month is not four weeks and the
# error compounds once you multiply by a year. 261 working days a year is the
# usual UK figure before holiday.
RUNS_PER_MONTH: dict[Period, float] = {
    Period.DAY: 365.25 / 12,
    Period.WORKING_DAY: 261.0 / 12,
    Period.WEEK: 365.25 / 84,
    Period.MONTH: 1.0,
}

PERIOD_LABEL: dict[Period, str] = {
    Period.DAY: "day",
    Period.WORKING_DAY: "working day",
    Period.WEEK: "week",
    Period.MONTH: "month",
}


class EffortInput(Base):
    """What the person told us. Nothing in here is guessed."""

    times_per_period: float = Field(gt=0, le=10_000)
    period: Period
    minutes_per_step: dict[Slug, float] = Field(
        default_factory=dict,
        description="Step id to roughly how many minutes that step takes each time.",
    )

    @model_validator(mode="after")
    def _durations_must_be_sane(self) -> EffortInput:
        for step_id, minutes in self.minutes_per_step.items():
            if minutes < 0:
                raise ValueError(f"Step '{step_id}' cannot take negative minutes.")
            if minutes > 8 * 60:
                raise ValueError(
                    f"Step '{step_id}' is listed at over eight hours. If a single step "
                    "really takes that long it is probably several steps."
                )
        return self

    @property
    def runs_per_month(self) -> float:
        return self.times_per_period * RUNS_PER_MONTH[self.period]


class StepEffort(Base):
    """One step's share of the time."""

    step_id: Slug
    verdict: Verdict
    minutes_each_time: float
    hours_per_month: float
    could_run_without_you: bool


class EffortSummary(Base):
    """The arithmetic. Plain multiplication, no cleverness."""

    runs_per_month: float
    hours_per_month: float
    hours_that_could_run_without_you: float
    hours_that_still_need_you: float
    steps: list[StepEffort]
    caveat: str

    # computed_field rather than a plain property so it appears in the JSON the
    # front end receives, instead of being silently dropped.
    @computed_field
    @property
    def percentage_automatable(self) -> int:
        if self.hours_per_month <= 0:
            return 0
        return round(100 * self.hours_that_could_run_without_you / self.hours_per_month)


# Guarded steps are counted as still needing you. Approving something takes less
# time than doing it, so the true saving sits somewhere above this figure, but
# any specific fraction we picked would be made up. Understating it is the
# honest direction to be wrong in.
CAVEAT = (
    "Steps that need a guard are counted as still needing you, because an approval "
    "is still your attention. Approving takes less time than doing, so the real "
    "saving is somewhere above this figure rather than below it."
)


def summarise_effort(effort: EffortInput, plan: AutomationPlan) -> EffortSummary:
    """Work out where the time goes, from the person's own numbers."""

    runs = effort.runs_per_month
    verdicts = {a.step_id: a.verdict for a in plan.assessments}

    steps: list[StepEffort] = []
    total = 0.0
    saved = 0.0

    for step_id, verdict in verdicts.items():
        minutes = float(effort.minutes_per_step.get(step_id, 0.0))
        hours = minutes * runs / 60.0
        without_you = verdict is Verdict.FULLY_AUTOMATABLE

        steps.append(
            StepEffort(
                step_id=step_id,
                verdict=verdict,
                minutes_each_time=minutes,
                hours_per_month=round(hours, 2),
                could_run_without_you=without_you,
            )
        )

        total += hours
        if without_you:
            saved += hours

    # Two decimal places, not one. A process taking ten minutes a month rounds
    # to 0.2 hours at one place, which is twenty per cent out. Presentation
    # decides how to show it; this keeps the number honest.
    return EffortSummary(
        runs_per_month=round(runs, 1),
        hours_per_month=round(total, 2),
        hours_that_could_run_without_you=round(saved, 2),
        hours_that_still_need_you=round(total - saved, 2),
        steps=steps,
        caveat=CAVEAT,
    )
