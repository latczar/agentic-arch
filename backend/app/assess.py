"""Stage two: judge a process graph and produce an automation plan.

Same shape as extract.py -- ask, validate, hand back the objections, retry.
The difference is that validation here is two-sided: the plan has to be
internally consistent (assessment.py's own rules) AND consistent with the graph
it claims to describe (validate_plan).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.extract import _readable_errors, _strip_fences
from app.llm.base import StructuredLLM
from app.schemas.assessment import AutomationPlan, validate_plan
from app.schemas.process import ProcessGraph

SYSTEM_PROMPT = """\
You advise small businesses on which parts of their work a computer could take
over. You are given a process someone already carries out by hand. Judge every
step of it.

For each step decide one of:

- fully_automatable      a computer can do this unattended
- automatable_with_control  a computer can do it, but something must guard it
- human_required         a person has to do this
- needs_more_info        you cannot tell yet, and you must say what is missing

Rules you must obey:

1. Assess every step in the process, exactly once, using the step ids given.
   Do not invent steps and do not leave any out.
2. Any step that moves money, cannot be undone, or carries legal or compliance
   weight is NEVER fully_automatable. Mark it automatable_with_control and give
   it a control.
3. automatable_with_control requires at least one control.
4. A threshold_approval control must carry a threshold saying where the limit
   is. Any other kind of control must not carry one.
5. needs_more_info requires at least one blocker saying what is missing.
6. fully_automatable cannot carry blockers.
7. A step the person described as their own judgement should not come back as
   fully_automatable unless you can state the rule that replaces the judgement.

Write rationales in plain British English, addressed to the person who does this
work today. One sentence each. No jargon, no sales language. If something should
not be automated, say so plainly rather than hedging.

8. A step that IS someone approving or checking something is human_required, not
   automatable_with_control. The step is already the control. Only use
   automatable_with_control for work a machine would do, that needs guarding.

Set biggest_win to the step id where automation would save the most tedium, and
write a headline that is honest about what will NOT be automated.

A worked example of a guarded step, showing the shape a control takes:

{
  "step_id": "pay_supplier",
  "verdict": "automatable_with_control",
  "rationale": "It can raise the payment itself, but money leaving the account
                should not be unattended.",
  "confidence": "high",
  "risks": ["moves_money", "irreversible"],
  "controls": [
    {
      "kind": "threshold_approval",
      "reason": "Anything over 5,000 waits for a person before it goes out.",
      "addresses": ["moves_money"],
      "threshold": {
        "field": "invoice.total",
        "operator": "gt",
        "value": "5000",
        "value_type": "money",
        "currency": "GBP"
      },
      "who_approves": "whoever owns the purchase ledger"
    }
  ],
  "blockers": []
}

And a worked example of a step that stays with a person:

{
  "step_id": "approve_large_invoice",
  "verdict": "human_required",
  "rationale": "This is you signing something off, which is the point of the step.",
  "confidence": "high",
  "risks": ["subjective_judgement"],
  "controls": [],
  "blockers": []
}
"""


@dataclass
class Attempt:
    number: int
    raw: str
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class AssessmentResult:
    plan: AutomationPlan | None
    attempts: list[Attempt]
    model: str

    @property
    def ok(self) -> bool:
        return self.plan is not None


def assess_process(
    graph: ProcessGraph,
    llm: StructuredLLM,
    max_attempts: int = 3,
) -> AssessmentResult:
    """Ask the model to judge each step, repairing the plan if it does not hold up."""

    schema = AutomationPlan.model_json_schema()
    prompt = _initial_prompt(graph)
    attempts: list[Attempt] = []
    previous_errors: list[str] | None = None

    for number in range(1, max_attempts + 1):
        raw = llm.generate_json(system=SYSTEM_PROMPT, prompt=prompt, schema=schema)
        attempt = Attempt(number=number, raw=raw)
        attempts.append(attempt)

        try:
            data = json.loads(_strip_fences(raw))
            plan = AutomationPlan.model_validate(_fill_missing_controls(data))
        except (ValidationError, ValueError) as exc:
            attempt.errors = _readable_errors(exc)
        else:
            # The plan parsed and is internally consistent. It still has to match
            # the process it is about -- every step assessed, nothing invented.
            attempt.errors = validate_plan(plan, graph)
            if not attempt.errors:
                return AssessmentResult(plan=plan, attempts=attempts, model=llm.name)

        # Identical complaints twice running means the repair is not landing.
        # Asking again costs quota and changes nothing.
        if attempt.errors == previous_errors:
            break

        previous_errors = attempt.errors
        prompt = _repair_prompt(graph, raw, attempt.errors)

    return AssessmentResult(plan=None, attempts=attempts, model=llm.name)


AUTO_CONTROL_REASON = (
    "Added automatically: this step was judged to need a guard, but none was "
    "specified. Defaulting to asking a person, which is the safe assumption."
)


def _fill_missing_controls(data: dict) -> dict:
    """Supply a conservative guard where one was called for but not given.

    Models reliably notice that paying an invoice is risky, and then, on the
    smaller ones, fail to attach the control that says so. Rejecting the whole
    analysis over that throws away work that was otherwise correct, and after a
    couple of rounds produces nothing at all.

    So this fails safe rather than closed. A step that needed an approval and
    gets one is right. A step that needed one and gets nothing is how money
    leaves an account unattended. The inserted control says plainly that it was
    added here rather than chosen by the model, because a guard rail nobody
    knows about is not much of a guard rail.
    """

    if not isinstance(data, dict):
        return data

    for assessment in data.get("assessments") or []:
        if not isinstance(assessment, dict):
            continue
        if assessment.get("verdict") != "automatable_with_control":
            continue
        if assessment.get("controls"):
            continue

        assessment["controls"] = [
            {
                "kind": "human_approval",
                "reason": AUTO_CONTROL_REASON,
                "addresses": list(assessment.get("risks") or []),
            }
        ]

    return data


def _process_summary(graph: ProcessGraph) -> str:
    """The graph as the model needs to see it: step ids, kinds, and what follows."""

    steps = []
    for step in graph.steps:
        steps.append(
            {
                "id": step.id,
                "name": step.name,
                "description": step.description,
                "kind": step.kind.value,
                "system": step.system_id,
                "iterates_over": step.iterates_over,
                "next": [
                    {"to": e.to_step, "when": e.condition}
                    for e in graph.outgoing(step.id)
                ],
            }
        )

    return json.dumps(
        {
            "title": graph.title,
            "summary": graph.summary,
            "trigger": graph.trigger.description,
            "systems": [{"id": s.id, "name": s.name} for s in graph.systems],
            "steps": steps,
        },
        indent=2,
    )


def _initial_prompt(graph: ProcessGraph) -> str:
    return (
        "Here is the process as it is carried out today:\n\n"
        f"{_process_summary(graph)}\n\n"
        "Assess every step."
    )


def _repair_prompt(graph: ProcessGraph, previous: str, errors: list[str]) -> str:
    complaints = "\n".join(f"- {e}" for e in errors)
    return (
        "Here is the process as it is carried out today:\n\n"
        f"{_process_summary(graph)}\n\n"
        f"You produced this assessment:\n\n{previous}\n\n"
        f"It does not satisfy the rules:\n\n{complaints}\n\n"
        "Produce the whole assessment again with every one of those fixed. "
        "Change nothing else."
    )
