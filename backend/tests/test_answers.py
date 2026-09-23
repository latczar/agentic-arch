"""Answering the questions it asks, and whether the answers survive the trip.

The tool has always ended by asking two or three things the description did not
say. Until now that was a dead end: good questions, nowhere to put the reply.

These cover the part that is easy to get wrong rather than the part that is easy
to write. Putting answers into the first prompt is three lines. Keeping them
there through a repair round is the bit that breaks silently, because the repair
prompt restates the description from scratch and a graph built without them
still validates perfectly.
"""

import json

from fastapi.testclient import TestClient

from app.api import app
from app.extract import extract_process
from app.schemas.process import Answer
from tests.test_extract import ScriptedLLM, graph_dict

APPROVAL = Answer(
    question="Do invoices need any manager approval before they are paid?",
    answer="Yes, anything over 5,000 waits for the branch manager.",
)
EMPTY_INBOX = Answer(
    question="What happens if there are no invoices on a Friday?",
    answer="I just finish for the day.",
)

DESCRIPTION = (
    "Every Friday I go through the supplier invoices in our shared inbox, read "
    "the amount off each one and pay it from the business account."
)


def test_an_answer_reaches_the_model_as_something_they_said():
    llm = ScriptedLLM([json.dumps(graph_dict())])
    extract_process(DESCRIPTION, llm, answers=[APPROVAL])

    prompt = llm.prompts[0]
    assert APPROVAL.question in prompt
    assert APPROVAL.answer in prompt
    assert "They said:" in prompt


def test_the_model_is_told_the_answers_are_fact_rather_than_suggestion():
    """A hint gets weighed against the description. A fact gets built in."""

    llm = ScriptedLLM([json.dumps(graph_dict())])
    extract_process(DESCRIPTION, llm, answers=[APPROVAL])

    prompt = llm.prompts[0]
    assert "fact about how the work is done" in prompt
    assert "Do not ask any of them again." in prompt


def test_answers_survive_a_repair_round():
    """The one that breaks quietly.

    A repair prompt restates the description from scratch. Drop the answers
    there and the second attempt produces a graph that ignores everything the
    person just told us, and it validates perfectly, so nothing complains.
    """

    llm = ScriptedLLM(
        [json.dumps(graph_dict(broken=True)), json.dumps(graph_dict())]
    )
    result = extract_process(DESCRIPTION, llm, answers=[APPROVAL, EMPTY_INBOX])

    assert result.ok
    assert len(llm.prompts) == 2, "expected a repair round to have happened"

    repair = llm.prompts[1]
    assert APPROVAL.answer in repair
    assert EMPTY_INBOX.answer in repair


def test_the_repair_prompt_still_carries_its_objections():
    """Adding the answers must not have pushed anything else out."""

    llm = ScriptedLLM(
        [json.dumps(graph_dict(broken=True)), json.dumps(graph_dict())]
    )
    extract_process(DESCRIPTION, llm, answers=[APPROVAL])

    repair = llm.prompts[1]
    assert "does not satisfy the rules" in repair
    assert "You produced this" in repair


def test_no_answers_adds_nothing_to_the_prompt():
    """The ordinary first run should look exactly as it always did."""

    with_none = ScriptedLLM([json.dumps(graph_dict())])
    extract_process(DESCRIPTION, with_none)

    assert "They have since been asked" not in with_none.prompts[0]
    assert with_none.prompts[0].strip().endswith(DESCRIPTION.strip()[-40:])


# --- Over the wire -----------------------------------------------------------


client = TestClient(app)


def test_the_endpoint_accepts_answers():
    response = client.post(
        "/api/analyse",
        json={
            "description": DESCRIPTION,
            "case": "payment-no-approval",
            "answers": [{"question": APPROVAL.question, "answer": APPROVAL.answer}],
        },
    )

    assert response.status_code == 200


def test_a_blank_answer_is_refused_rather_than_sent_as_an_empty_fact():
    """An empty string reads to the model as the answer being 'nothing'."""

    response = client.post(
        "/api/analyse",
        json={
            "description": DESCRIPTION,
            "answers": [{"question": APPROVAL.question, "answer": ""}],
        },
    )

    assert response.status_code == 422


def test_answers_are_capped():
    """A list endpoint with no limit is somebody else's prompt budget."""

    response = client.post(
        "/api/analyse",
        json={
            "description": DESCRIPTION,
            "answers": [
                {"question": f"Question {n}?", "answer": "Yes."} for n in range(20)
            ],
        },
    )

    assert response.status_code == 422
