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


def test_the_export_endpoint_returns_an_importable_workflow():
    analysis = client.post(
        "/api/analyse", json={"description": INVOICE, "case": "payment-no-approval"}
    ).json()

    workflow = client.post(
        "/api/export/n8n",
        json={"graph": analysis["graph"], "plan": analysis["plan"]},
    ).json()

    assert workflow["name"]
    assert workflow["nodes"]

    declared = {n["name"] for n in workflow["nodes"]}
    for source, connection in workflow["connections"].items():
        assert source in declared
        for output in connection["main"]:
            for target in output:
                assert target["node"] in declared

    # The guarded payment step should have picked up a real Wait node.
    assert any(n["type"] == "n8n-nodes-base.wait" for n in workflow["nodes"])


def test_exporting_without_a_plan_still_works():
    analysis = client.post(
        "/api/analyse", json={"description": INVOICE, "case": "invoice-with-approval"}
    ).json()

    workflow = client.post(
        "/api/export/n8n", json={"graph": analysis["graph"]}
    ).json()

    assert workflow["nodes"]


def analysed(case: str = "invoice-with-approval") -> dict:
    return client.post("/api/analyse", json={"description": INVOICE, "case": case}).json()


def test_the_effort_endpoint_multiplies_the_users_own_numbers():
    analysis = analysed()
    steps = [s["id"] for s in analysis["graph"]["steps"]]

    summary = client.post(
        "/api/effort",
        json={
            "plan": analysis["plan"],
            "effort": {
                "times_per_period": 10,
                "period": "week",
                "minutes_per_step": {step: 3 for step in steps},
            },
        },
    ).json()

    # 10 a week is about 43.5 a month; 3 minutes on each of N steps.
    expected = 43.5 * 3 * len(steps) / 60
    assert abs(summary["hours_per_month"] - expected) < 0.5
    assert summary["percentage_automatable"] > 0
    assert summary["caveat"]


def test_effort_rejects_an_impossible_duration():
    analysis = analysed()
    response = client.post(
        "/api/effort",
        json={
            "plan": analysis["plan"],
            "effort": {
                "times_per_period": 1,
                "period": "week",
                "minutes_per_step": {"check_emails": 9999},
            },
        },
    )
    assert response.status_code == 422


def test_an_analysis_can_be_shared_and_read_back():
    analysis = analysed()

    created = client.post(
        "/api/share",
        json={"graph": analysis["graph"], "plan": analysis["plan"]},
    ).json()
    assert created["id"]

    shared = client.get(f"/api/share/{created['id']}").json()
    assert shared["title"] == analysis["graph"]["title"]
    assert shared["graph"]["steps"] == analysis["graph"]["steps"]
    assert shared["plan"]["assessments"]


def test_a_share_can_carry_the_time_figures_too():
    analysis = analysed()
    steps = [s["id"] for s in analysis["graph"]["steps"]]

    created = client.post(
        "/api/share",
        json={
            "graph": analysis["graph"],
            "plan": analysis["plan"],
            "effort": {
                "times_per_period": 4,
                "period": "week",
                "minutes_per_step": {step: 5 for step in steps},
            },
        },
    ).json()

    shared = client.get(f"/api/share/{created['id']}").json()
    assert shared["effort"]["times_per_period"] == 4


def test_an_unknown_share_says_so_rather_than_failing_oddly():
    response = client.get("/api/share/nope")
    assert response.status_code == 404
    assert "expired" in response.json()["detail"]
