"""The question the code asks when the model forgets to.

Both directions, like the guard cases: it has to ask when a process waits on
somebody and nothing covers them staying silent, and it has to stay quiet when
the person already said, the model already asked, or nobody outside is involved.
"""

from app.questions import add_missing_questions
from app.schemas.process import Answer, ProcessGraph

DEPOSIT = "When a tenancy ends I send the deductions to the tenant to agree."


def graph(decision_text: str = "Check whether the tenant agreed to the deductions",
          kind: str = "decision", questions: list | None = None) -> ProcessGraph:
    steps = [
        {"id": "send_deductions", "name": "Send deductions to the tenant", "kind": "notify",
         "description": "Email the breakdown."},
        {"id": "check_reply", "name": "Check the reply", "kind": kind,
         "description": decision_text},
        {"id": "release", "name": "Release the remaining deposit", "kind": "write",
         "description": "Through the scheme."},
        {"id": "adjudicate", "name": "Hand over to the adjudicator", "kind": "notify",
         "description": "The scheme decides."},
    ]
    edges = [{"from_step": "send_deductions", "to_step": "check_reply"}]
    if kind == "decision":
        edges += [
            {"from_step": "check_reply", "to_step": "release", "condition": "they agree"},
            {"from_step": "check_reply", "to_step": "adjudicate", "condition": "they disagree"},
        ]
    else:
        # A wait has one way out, so the adjudicator would be unreachable, and
        # the graph checker rightly refuses a step nothing leads to.
        steps = [s for s in steps if s["id"] != "adjudicate"]
        edges += [{"from_step": "check_reply", "to_step": "release"}]
    return ProcessGraph.model_validate({
        "title": "Deposit return",
        "summary": "Returning a deposit.",
        "trigger": {"description": "A tenancy ends", "kind": "manual",
                    "first_step_id": "send_deductions"},
        "steps": steps,
        "edges": edges,
        "questions": questions or [],
    })


def added(g: ProcessGraph):
    return [q for q in g.questions if q.added_by_us]


def test_asks_what_happens_when_the_tenant_never_replies():
    result = add_missing_questions(graph(), DEPOSIT)

    [question] = added(result)
    assert question.question == "What happens if the tenant never replies?"
    assert question.affects_step_ids == ["check_reply"]
    assert len(question.suggested_answers) >= 2


def test_a_wait_on_somebody_outside_is_enough():
    result = add_missing_questions(graph("Wait for the landlord to reply", kind="wait"), DEPOSIT)
    assert added(result)[0].question == "What happens if the landlord never replies?"


def test_stays_quiet_when_the_description_already_says():
    said = DEPOSIT + " If they do not reply within a week I chase them."
    assert not added(add_missing_questions(graph(), said))


def test_stays_quiet_when_the_model_already_asked():
    asked = [{"id": "no_answer", "question": "What if the tenant never responds?",
              "why_it_matters": "It decides the timeout."}]
    assert not added(add_missing_questions(graph(questions=asked), DEPOSIT))


def test_stays_quiet_once_the_person_has_answered_it():
    answers = [Answer(question="What happens if the tenant never replies?",
                      answer="We chase after five days")]
    assert not added(add_missing_questions(graph(), DEPOSIT, answers))


def test_stays_quiet_on_a_decision_about_a_figure():
    result = add_missing_questions(graph("Check whether the deductions are over 500 pounds"), DEPOSIT)
    assert not added(result)


def test_the_model_cannot_mark_its_own_question_as_ours():
    """The same rule as the override record: only code may claim to be code."""

    claimed = [{"id": "sneaky", "question": "Is there a deadline for the tenant?",
                "why_it_matters": "Timing.", "added_by_us": True}]
    result = add_missing_questions(graph(questions=claimed), DEPOSIT)

    mine = {q.id: q.added_by_us for q in result.questions}
    assert mine["sneaky"] is False


def test_the_graph_still_validates_with_the_question_added():
    result = add_missing_questions(graph(), DEPOSIT)
    assert ProcessGraph.model_validate(result.model_dump()) is not None
