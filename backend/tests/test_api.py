"""Tests for the HTTP layer, driven entirely off recorded cases.

No key, no network, no quota, and the same assertions run in CI as locally.
"""

from fastapi.testclient import TestClient

from app.api import app

client = TestClient(app)

INVOICE = (
    "Every morning I go through my emails looking for invoices. When I find one I "
    "download the PDF attachment, read the total off it, and type that into our "
    "Google Sheet."
)


def test_health_lists_the_replayable_cases():
    body = client.get("/api/health").json()
    assert body["ok"]
    assert "invoice-with-approval" in body["cases"]


def test_examples_say_which_can_be_replayed():
    examples = client.get("/api/examples").json()["examples"]
    assert len(examples) == 2
    assert all(e["replayable"] for e in examples)
    assert all(len(e["description"]) > 50 for e in examples)


def test_analysing_a_replayed_case_returns_a_graph_and_a_plan():
    body = client.post(
        "/api/analyse", json={"description": INVOICE, "case": "invoice-with-approval"}
    ).json()

    assert body["ok"]
    assert body["graph"]["steps"]
    assert body["plan"]["assessments"]
    assert body["extraction_attempts"][0]["ok"]
    assert body["assessment_attempts"][0]["ok"]


def test_the_guarded_case_comes_back_with_a_control():
    body = client.post(
        "/api/analyse", json={"description": INVOICE, "case": "payment-no-approval"}
    ).json()

    assert body["ok"]
    guarded = [
        a for a in body["plan"]["assessments"]
        if a["verdict"] == "automatable_with_control"
    ]
    assert guarded, "the guarded case should produce at least one guarded step"
    assert all(a["controls"] for a in guarded)


def test_an_unknown_case_is_reported_not_crashed():
    body = client.post(
        "/api/analyse", json={"description": INVOICE, "case": "does-not-exist"}
    ).json()

    assert not body["ok"]
    assert "does-not-exist" in body["error"]


def test_a_too_short_description_is_rejected():
    assert client.post("/api/analyse", json={"description": "hi"}).status_code == 422
