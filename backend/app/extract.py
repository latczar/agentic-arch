"""Turn a plain-English description of a process into a validated ProcessGraph.

This is where the model and the checker meet. The shape is:

    ask -> parse -> validate -> if broken, tell it what broke and ask again

Two things are worth knowing about the prompt below. First, it teaches the model
the same rules the validator enforces. Every rule we state here is a repair we
do not have to pay for later. Second, it does not try to be clever. There
are no few-shot examples yet, because we have not yet seen which mistakes the model
actually makes. Adding examples before you have evidence is guessing.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.llm.base import StructuredLLM
from app.questions import add_missing_questions
from app.schemas.process import Answer, ProcessGraph

SYSTEM_PROMPT = """\
You map out business processes. Someone describes repetitive work they do by
hand, and you turn that description into a structured process graph.

You are describing what they do TODAY. Do not improve it, do not suggest
automation, do not skip steps because they seem obvious. That judgement happens
later and somewhere else.

Rules the output must obey:

1. Every step needs a short id in lower_snake_case, unique within the process.
2. Edges connect steps by id. Both ends must be steps you have declared.
3. A step where the process splits must have kind "decision". A decision step
   must have at least TWO outgoing edges, and every one of those edges must
   carry a `condition` saying what sends the process that way.
4. Any step that is NOT a decision may have at most one outgoing edge, and that
   edge must not carry a condition.
5. The process must not loop back on itself. If a step repeats once per item
   ("for each attachment"), say so in that step's `iterates_over` field rather
   than drawing an edge backwards.
6. Every step must be reachable from `trigger.first_step_id`, and at least one
   step must be an ending with no outgoing edge.
7. Every system a step refers to must be declared in `systems` first.

On things the description does not say:

- Do not invent detail. If you had to assume something to draw the flow, put it
  in that step's `assumption` field.
- Raise it as a clarifying question too, but only when the answer would
  genuinely change the design. Two or three questions is plenty. Offer likely
  answers so it can be answered in one click.
- Where the process waits on somebody's reply and the description does not say
  what happens if they never answer, that is a missing branch. Do not draw it;
  ask about it.

Write names and descriptions in plain British English, the way the person would
say it themselves. Keep it short.
"""


@dataclass
class Attempt:
    """One round trip to the model, kept so we can see what happened."""

    number: int
    raw: str
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class ExtractionResult:
    graph: ProcessGraph | None
    attempts: list[Attempt]
    model: str

    @property
    def ok(self) -> bool:
        return self.graph is not None


def extract_process(
    description: str,
    llm: StructuredLLM,
    max_attempts: int = 3,
    answers: Sequence[Answer] = (),
) -> ExtractionResult:
    """Ask the model for a process graph, repairing it if the checker objects."""

    schema = ProcessGraph.model_json_schema()
    prompt = _first_prompt(description, answers)
    attempts: list[Attempt] = []
    previous_errors: list[str] | None = None

    for number in range(1, max_attempts + 1):
        raw = llm.generate_json(system=SYSTEM_PROMPT, prompt=prompt, schema=schema)
        attempt = Attempt(number=number, raw=raw)
        attempts.append(attempt)

        try:
            graph = ProcessGraph.model_validate_json(_strip_fences(raw))
        except (ValidationError, ValueError) as exc:
            attempt.errors = _readable_errors(exc)

            # Identical complaints twice running means the repair is not
            # landing. Either the message is not actionable or the model cannot
            # see a way out. Asking again costs quota and changes nothing.
            if attempt.errors == previous_errors:
                break

            previous_errors = attempt.errors
            prompt = _repair_prompt(description, raw, attempt.errors, answers)
            continue

        # After validation, so a question we add can only point at a step that
        # really exists, and cannot be what made a map fail.
        graph = add_missing_questions(graph, description, answers)
        return ExtractionResult(graph=graph, attempts=attempts, model=llm.name)

    return ExtractionResult(graph=None, attempts=attempts, model=llm.name)


def _stated_facts(answers: Sequence[Answer]) -> str:
    """The answers as settled fact, or nothing at all.

    Written as things the person has told us rather than as hints. A question
    somebody has already answered coming back a second time reads as not having
    been listened to, and it is the commonest way a loop like this disappoints.
    """

    if not answers:
        return ""

    lines = "\n".join(f"- {a.question}\n  They said: {a.answer}" for a in answers)
    return (
        "\n\nThey have since been asked about the gaps, and answered:\n\n"
        f"{lines}\n\n"
        "Every answer above is a fact about how the work is done, exactly as much "
        "as the description is. Build them into the process. Do not ask any of "
        "them again."
    )


def _first_prompt(description: str, answers: Sequence[Answer] = ()) -> str:
    return (
        f"Here is the process, in the person's own words:\n\n{description.strip()}"
        f"{_stated_facts(answers)}"
    )


def _repair_prompt(
    description: str,
    previous: str,
    errors: list[str],
    answers: Sequence[Answer] = (),
) -> str:
    """Hand the model its own output back, with every objection at once.

    Showing it what it produced matters. Without that, it re-derives the answer
    from scratch and often reintroduces the fault it just fixed.

    The answers go back in too. This prompt restates the description from
    scratch, so leaving them out would silently discard them on the second
    attempt and hand back a graph that ignores everything the person said.
    """

    complaints = "\n".join(f"- {e}" for e in errors)
    return (
        f"{_first_prompt(description, answers)}\n\n"
        f"You produced this:\n\n{previous}\n\n"
        f"It does not satisfy the rules:\n\n{complaints}\n\n"
        "Produce the whole graph again with every one of those fixed. Change "
        "nothing else."
    )


def _strip_fences(raw: str) -> str:
    """Remove markdown code fences if the model added them anyway."""

    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _readable_errors(exc: Exception) -> list[str]:
    """Flatten an exception into plain lines a model can act on.

    Pydantic nests its errors; our graph checker returns a bullet list inside a
    single message. Both end up as one flat list, because that is what goes
    into the repair prompt.
    """

    if isinstance(exc, ValidationError):
        lines: list[str] = []
        for err in exc.errors():
            location = ".".join(str(p) for p in err["loc"]) or "(root)"
            message = err["msg"].removeprefix("Value error, ")
            if "\n" in message:
                # Our own multi-fault graph message. Split it back out.
                head, *bullets = message.splitlines()
                lines.extend(b.strip().lstrip("- ") for b in bullets if b.strip())
                if not bullets:
                    lines.append(f"{location}: {head}")
            else:
                lines.append(f"{location}: {message}")
        return lines

    if isinstance(exc, json.JSONDecodeError):
        return [f"The response was not valid JSON: {exc}"]

    return [str(exc)]
