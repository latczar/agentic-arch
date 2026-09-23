"""A question the code asks when the model forgets to.

The same arrangement as the guards in assess.py, applied to the map. Asked to
raise what a description leaves out, the model usually spots a missing approval
and usually misses the reply that never comes. In three live runs of a deposit
return it asked nothing about a tenant who never answers, though the whole
process waits on them. That is exactly the step nobody mentions, and an
automation built without it waits indefinitely.

So when a step waits on, or decides on, somebody outside the business, and
nothing already covers them staying silent, this asks. The question is marked as
ours on the page, and the model cannot claim the mark: whatever it put in that
field is cleared before anything is added.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.schemas.assessment import OUTSIDE_WORDS, REPLY_WORDS, words_in
from app.schemas.process import Answer, ClarifyingQuestion, ProcessGraph, Step, StepKind

# Any of these in the description, a question or an answer means silence is
# already covered, and asking about something the person already told us is how
# a tool shows it was not listening. Deliberately generous: staying quiet when we
# should have asked costs one question, asking about what they said costs trust.
SILENCE_WORDS = frozenset(
    {
        "chase", "chases", "chasing", "chased",
        "remind", "reminds", "reminding", "reminded", "reminder", "reminders",
        "never", "silence", "silent", "ignore", "ignores", "ignored",
        "hear",
    }
)


def _party(step: Step) -> str | None:
    """Who this step waits on or decides about, if it is somebody outside."""

    words = words_in(f"{step.name} {step.description}")
    party = next((w for w in words if w in OUTSIDE_WORDS), None)
    if party is None:
        return None

    waits = step.kind is StepKind.WAIT
    decides_on_reply = step.kind is StepKind.DECISION and bool(set(words) & REPLY_WORDS)
    if not (waits or decides_on_reply):
        return None

    # "tenants" reads badly in "if the tenants never replies".
    singular = party[:-1]
    return singular if party.endswith("s") and singular in OUTSIDE_WORDS else party


def _covered(description: str, graph: ProcessGraph, answers: Sequence[Answer]) -> bool:
    texts = [description]
    texts += [f"{q.question} {q.why_it_matters}" for q in graph.questions]
    texts += [f"{a.question} {a.answer}" for a in answers]
    return any(set(words_in(text)) & SILENCE_WORDS for text in texts)


def add_missing_questions(
    graph: ProcessGraph, description: str, answers: Sequence[Answer] = ()
) -> ProcessGraph:
    """The graph, plus a question about silence if it needs one and lacks it."""

    # Only our code may say a question came from our code.
    for question in graph.questions:
        question.added_by_us = False

    if _covered(description, graph, answers):
        return graph

    step, party = next(
        ((s, p) for s in graph.steps if (p := _party(s))), (None, None)
    )
    if step is None:
        return graph

    taken = {q.id for q in graph.questions}
    question_id = f"no_reply_{step.id}"[:64]
    if question_id in taken:
        return graph

    graph.questions.append(
        ClarifyingQuestion(
            id=question_id,
            question=f"What happens if the {party} never replies?",
            why_it_matters=(
                f"The process waits on the {party}, and nothing says what happens "
                "if no answer comes. Left out, an automation would wait indefinitely."
            ),
            affects_step_ids=[step.id],
            suggested_answers=[
                "Chase them after a few days",
                "A person follows it up by phone",
                "It has never come up",
            ],
            added_by_us=True,
        )
    )
    return graph
