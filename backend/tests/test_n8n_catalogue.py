"""Which steps earn a real node, and more importantly which do not.

Half of these check that nothing is emitted. A catalogue that maps everything is
a catalogue that invents things, and an invented node type pastes into n8n as a
broken node with no explanation of what it was meant to be.
"""

import pytest

from app.n8n_catalogue import choose_node
from app.schemas.common import System, SystemCategory
from app.schemas.process import Edge, ProcessGraph, Step, StepKind, Trigger, TriggerKind


def graph_with(step: Step, systems: list[System] | None = None) -> ProcessGraph:
    return ProcessGraph(
        title="A process",
        summary="Something done by hand.",
        trigger=Trigger(
            kind=TriggerKind.SCHEDULE, description="every morning", first_step_id=step.id
        ),
        systems=systems or [],
        steps=[step],
        edges=[],
    )


def step_in(
    name: str,
    kind: StepKind,
    system: System | None = None,
    description: str = "",
) -> tuple[Step, ProcessGraph]:
    step = Step(
        id="a_step",
        name=name,
        description=description or name,
        kind=kind,
        system_id=system.id if system else None,
    )
    return step, graph_with(step, [system] if system else [])


SHEETS = System(id="the_sheet", name="Google Sheet", category=SystemCategory.SPREADSHEET)
SLACK = System(id="team_chat", name="Slack", category=SystemCategory.CHAT)
GMAIL = System(id="mailbox", name="Gmail", category=SystemCategory.EMAIL)
VAGUE_SHEET = System(id="the_sheet", name="spreadsheet", category=SystemCategory.SPREADSHEET)
VAGUE_MAIL = System(id="mailbox", name="Email", category=SystemCategory.EMAIL)


# --- Where the person named the system ---------------------------------------


def test_a_named_google_sheet_becomes_a_sheets_node():
    step, graph = step_in("Type the total into the sheet", StepKind.WRITE, SHEETS)
    choice = choose_node(step, graph, "write row to spreadsheet")

    assert choice is not None
    assert choice.type == "n8n-nodes-base.googleSheets"


def test_a_named_slack_becomes_a_slack_node():
    step, graph = step_in("Message accounting", StepKind.NOTIFY, SLACK)
    assert choose_node(step, graph, "send chat message").type == "n8n-nodes-base.slack"


def test_a_named_gmail_becomes_a_gmail_node():
    step, graph = step_in("Check for new invoices", StepKind.READ, GMAIL)
    assert choose_node(step, graph, "read new email").type == "n8n-nodes-base.gmail"


# --- Where they did not ------------------------------------------------------


def test_an_unnamed_spreadsheet_stays_a_placeholder():
    """It could be Excel, Numbers or a CSV, and picking one would invent a fact."""

    step, graph = step_in("Mark it as paid in the spreadsheet", StepKind.WRITE, VAGUE_SHEET)
    assert choose_node(step, graph, "update spreadsheet row") is None


def test_unspecified_email_stays_a_placeholder():
    """Gmail, Outlook and IMAP are a real choice the person has not made."""

    step, graph = step_in("Open the shared inbox", StepKind.READ, VAGUE_MAIL)
    assert choose_node(step, graph, "read new email") is None


def test_a_step_with_no_system_at_all_stays_a_placeholder():
    step, graph = step_in("Read the amount off the invoice", StepKind.EXTRACT)
    assert choose_node(step, graph, "extract text from document") is None


def test_a_bank_payment_stays_a_placeholder():
    """The riskiest step in the suite, and there is no node that does it."""

    step, graph = step_in("Pay the invoice through the banking portal", StepKind.WRITE)
    assert choose_node(step, graph, "initiate bank payment") is None


# --- Steps that are not nodes ------------------------------------------------


@pytest.mark.parametrize("kind", [StepKind.JUDGEMENT, StepKind.WAIT])
def test_steps_that_are_not_integrations_get_nothing(kind: StepKind):
    """A person deciding is not a node, and neither is waiting for one."""

    step, graph = step_in("Check with the manager", kind, SHEETS)
    assert choose_node(step, graph, "read new email") is None


def test_a_decision_gets_nothing_because_it_is_already_an_if():
    """Built out properly, since the validator rightly refuses a lone decision."""

    branch = Step(
        id="over_limit",
        name="Is it over the limit",
        description="Check the amount.",
        kind=StepKind.DECISION,
        system_id=SHEETS.id,
    )
    yes = Step(id="ask_boss", name="Ask the manager", description="...", kind=StepKind.JUDGEMENT)
    no = Step(id="carry_on", name="Put it through", description="...", kind=StepKind.WRITE)

    graph = ProcessGraph(
        title="A process",
        summary="Something done by hand.",
        trigger=Trigger(
            kind=TriggerKind.SCHEDULE, description="every morning", first_step_id=branch.id
        ),
        systems=[SHEETS],
        steps=[branch, yes, no],
        edges=[
            Edge(from_step=branch.id, to_step=yes.id, condition="over the limit"),
            Edge(from_step=branch.id, to_step=no.id, condition="under the limit"),
        ],
    )

    assert choose_node(branch, graph, "evaluate numeric condition") is None


# --- The generic ones --------------------------------------------------------


def test_a_transformation_becomes_a_code_node():
    """No credentials, nothing to guess, and the step says what to compute."""

    step, graph = step_in("Calculate the total due", StepKind.TRANSFORM)
    assert choose_node(step, graph, "calculate a sum").type == "n8n-nodes-base.code"


def test_an_api_call_becomes_an_http_request():
    step, graph = step_in("Pull the report", StepKind.READ)
    assert choose_node(step, graph, "rest api call").type == "n8n-nodes-base.httpRequest"


# --- Guarding the catalogue itself -------------------------------------------


def test_every_node_type_looks_like_an_n8n_node():
    """Cheap shape check. The real names were verified against n8n's own docs."""

    from app.n8n_catalogue import BY_CAPABILITY, BY_SYSTEM

    for _, _, choice in (*BY_SYSTEM, *BY_CAPABILITY):
        assert choice.type.startswith("n8n-nodes-base.")
        assert choice.type.count(".") == 1
        assert choice.note.strip(), f"{choice.type} does not say what is left to do"


def test_the_system_signal_beats_the_capability_signal():
    """A named system is close to a stated fact. A capability is a description."""

    step, graph = step_in("Post the summary", StepKind.NOTIFY, SLACK)
    assert choose_node(step, graph, "send an email").type == "n8n-nodes-base.slack"
