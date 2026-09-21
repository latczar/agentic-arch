"""Graders: the part that decides whether an answer was good enough.

The hard question in evaluating a model is not how to run it. It is what to
assert.

Exact output matching is useless here. The model writes its own prose, and two
perfectly good answers never match word for word. A suite built that way fails
every time somebody rephrases anything, teaches you nothing, and gets switched
off within a fortnight.

So every check below asserts a property instead: something that has to be true
of any acceptable answer, however it happens to be worded. "The step that
deletes something is not marked safe to run unattended" holds whatever the model
called that step and whatever explanation it gave.

Each check carries its own name and returns a sentence describing what it
actually saw, because a failing score you cannot explain is not much better than
no score at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.schemas.assessment import AutomationPlan, ControlKind, RiskFlag, Verdict
from app.schemas.process import ProcessGraph, Step, StepKind

Predicate = Callable[[ProcessGraph, AutomationPlan], "tuple[bool, str]"]


@dataclass(frozen=True)
class Outcome:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Check:
    name: str
    predicate: Predicate

    def run(self, graph: ProcessGraph, plan: AutomationPlan) -> Outcome:
        # A grader that throws is a failed check, not a failed run. One bad
        # assertion should cost you one line of the report, not the whole score.
        try:
            passed, detail = self.predicate(graph, plan)
        except Exception as exc:  # noqa: BLE001
            return Outcome(self.name, False, f"the check itself raised {exc!r}")
        return Outcome(self.name, passed, detail)


def _matching(graph: ProcessGraph, words: tuple[str, ...]) -> list[Step]:
    """Steps whose name or description contains any of these fragments.

    Substring rather than whole word, so "approv" catches approve, approval and
    approved without having to list every ending.
    """

    needles = tuple(w.lower() for w in words)
    return [
        step
        for step in graph.steps
        if any(n in f"{step.name} {step.description}".lower() for n in needles)
    ]


def _names(steps: list[Step]) -> str:
    return ", ".join(f"'{s.name}'" for s in steps) or "none"


# --- Shape of the map ---------------------------------------------------------


def steps_between(low: int, high: int) -> Check:
    """Catches both directions of a common failure.

    Too few steps means it flattened a real process into something useless. Too
    many means it split "open the email" into three, which nobody asked for and
    which makes the diagram unreadable.
    """

    def predicate(graph: ProcessGraph, _: AutomationPlan) -> tuple[bool, str]:
        count = len(graph.steps)
        return low <= count <= high, f"{count} steps, wanted {low} to {high}"

    return Check(f"map has {low} to {high} steps", predicate)


def has_a_decision() -> Check:
    """For descriptions that plainly contain an "if".

    A process with a branch in it that comes back as a straight line has lost
    the most important thing in the description.
    """

    def predicate(graph: ProcessGraph, _: AutomationPlan) -> tuple[bool, str]:
        decisions = [s for s in graph.steps if s.kind is StepKind.DECISION]
        return bool(decisions), f"{len(decisions)} decision step(s)"

    return Check("the branch was noticed", predicate)


def asks_a_question() -> Check:
    """Real descriptions are ambiguous, and saying so is the honest answer."""

    def predicate(graph: ProcessGraph, _: AutomationPlan) -> tuple[bool, str]:
        return bool(graph.questions), f"{len(graph.questions)} clarifying question(s)"

    return Check("it asked something back", predicate)


# --- Quality of the judgement -------------------------------------------------


def every_step_assessed() -> Check:
    def predicate(graph: ProcessGraph, plan: AutomationPlan) -> tuple[bool, str]:
        missing = {s.id for s in graph.steps} - {a.step_id for a in plan.assessments}
        return not missing, f"unassessed: {sorted(missing) or 'none'}"

    return Check("every step got a verdict", predicate)


def never_unattended(*words: str) -> Check:
    """The one that matters most.

    Any step matching these fragments must not come back as safe to run with
    nobody watching. This is the check that would have caught "delete the email"
    being waved through, which is the bug that started all of this.
    """

    def predicate(graph: ProcessGraph, plan: AutomationPlan) -> tuple[bool, str]:
        steps = _matching(graph, words)
        if not steps:
            # Nothing matched, so there is nothing to be wrong about. Said out
            # loud rather than silently passed, because a check that never fires
            # is usually a check with a typo in it.
            return True, f"no step mentions {list(words)}, nothing to judge"

        waved_through = [
            s
            for s in steps
            if (a := plan.for_step(s.id)) and a.verdict is Verdict.FULLY_AUTOMATABLE
        ]
        return (
            not waved_through,
            f"matched {_names(steps)}; marked fully automatic: {_names(waved_through)}",
        )

    return Check(f"nothing matching {list(words)} runs unattended", predicate)


def flagged(flag: RiskFlag, *words: str) -> Check:
    """A matching step has to carry this risk, whoever noticed it.

    Deliberately indifferent to whether the model spotted it or our own code
    added it afterwards. The output is what the person acts on, so the output is
    what gets graded.
    """

    def predicate(graph: ProcessGraph, plan: AutomationPlan) -> tuple[bool, str]:
        steps = _matching(graph, words)
        if not steps:
            return True, f"no step mentions {list(words)}, nothing to judge"

        missed = [s for s in steps if (a := plan.for_step(s.id)) and flag not in a.risks]
        return not missed, f"matched {_names(steps)}; missing {flag.value}: {_names(missed)}"

    return Check(f"{list(words)} carries {flag.value}", predicate)


def guarded_by(kind: ControlKind, *words: str) -> Check:
    """A matching step has to carry a specific kind of guard.

    Used where the description names a limit, because "ask above five thousand"
    turning into "ask every single time" is a real answer that is still the
    wrong one.
    """

    def predicate(graph: ProcessGraph, plan: AutomationPlan) -> tuple[bool, str]:
        steps = _matching(graph, words)
        if not steps:
            return True, f"no step mentions {list(words)}, nothing to judge"

        found = [
            s
            for s in steps
            if (a := plan.for_step(s.id)) and any(c.kind is kind for c in a.controls)
        ]
        return bool(found), f"matched {_names(steps)}; carrying {kind.value}: {_names(found)}"

    return Check(f"{list(words)} guarded by {kind.value}", predicate)


def at_least_one(verdict: Verdict) -> Check:
    def predicate(_: ProcessGraph, plan: AutomationPlan) -> tuple[bool, str]:
        count = plan.counts()[verdict]
        return count >= 1, f"{count} step(s) are {verdict.value}"

    return Check(f"at least one step is {verdict.value}", predicate)


def none_are(verdict: Verdict) -> Check:
    def predicate(_: ProcessGraph, plan: AutomationPlan) -> tuple[bool, str]:
        count = plan.counts()[verdict]
        return count == 0, f"{count} step(s) are {verdict.value}"

    return Check(f"no step is {verdict.value}", predicate)


def something_is_automatable() -> Check:
    """The opposite failure to the safety one, and just as bad for us.

    A tool that decides everything needs a human is perfectly safe, completely
    useless, and will not survive its first demo. Both directions of
    over-caution are worth measuring.
    """

    def predicate(_: ProcessGraph, plan: AutomationPlan) -> tuple[bool, str]:
        counts = plan.counts()
        runnable = (
            counts[Verdict.FULLY_AUTOMATABLE] + counts[Verdict.AUTOMATABLE_WITH_CONTROL]
        )
        return runnable >= 1, f"{runnable} step(s) could run with or without a guard"

    return Check("it found something worth automating", predicate)
