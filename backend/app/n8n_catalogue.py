"""Which real n8n node should stand in for a step.

The model already works out what each step needs to be able to do. It fills in a
capability ("read new email", "write row to spreadsheet") and suggests tools for
it. That output is good, and it is not safe to use directly: asked for tools, it
offers "Zapier file download" and "Make.com iterator", which are other people's
products, and asked for n8n node names it would produce plausible ones that do
not exist. A workflow containing an invented node type pastes into n8n as a
broken node with no explanation.

So the split is the same as everywhere else here. The model says what the step
needs to do. This file decides what that maps to, from a fixed list of nodes
checked against n8n's own documentation. Anything that does not map confidently
stays a placeholder, because a placeholder saying what to put there is more
useful than a node that does not exist.

The strongest signal is not the capability, it is the system the person named.
"I type that into our Google Sheet" is not a guess about Google Sheets. Where
somebody said only "email", it stays a placeholder, because Gmail, Outlook and
IMAP are a real choice and picking one for them would be inventing a fact.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.process import ProcessGraph, Step, StepKind


@dataclass(frozen=True)
class NodeChoice:
    """A real n8n node to emit in place of a placeholder."""

    type: str
    label: str
    # Everything worth saying still needs doing once it is on their canvas.
    note: str
    # Emitted as typeVersion. Deliberately 1 across the board: no parameters are
    # set, so the version only decides which iteration of the node's own screen
    # opens, and the first one is the one most certain to exist in any install.
    version: int = 1


# Verified against n8n's documentation rather than recalled. A wrong string here
# is the one failure that makes the whole paste look broken, so the list is
# short on purpose and grows only when something has been checked.
GMAIL = NodeChoice(
    "n8n-nodes-base.gmail",
    "Gmail",
    "Connect a Gmail credential and pick the operation. The step description above says what it is doing.",
)
GOOGLE_SHEETS = NodeChoice(
    "n8n-nodes-base.googleSheets",
    "Google Sheets",
    "Connect a Google credential, then choose the spreadsheet, the sheet and the columns.",
)
SLACK = NodeChoice(
    "n8n-nodes-base.slack",
    "Slack",
    "Connect a Slack credential and choose the channel to post to.",
)
SEND_EMAIL = NodeChoice(
    "n8n-nodes-base.emailSend",
    "Send Email",
    "Add SMTP credentials, or swap this for the node matching your mail provider.",
)
HTTP = NodeChoice(
    "n8n-nodes-base.httpRequest",
    "HTTP Request",
    "Set the URL, method and authentication for the system this talks to.",
    version=4,
)
CODE = NodeChoice(
    "n8n-nodes-base.code",
    "Code",
    "Write the transformation here. No credentials needed.",
    version=2,
)
SET = NodeChoice(
    "n8n-nodes-base.set",
    "Edit Fields",
    "Map the fields this step produces. No credentials needed.",
)

# Matched against the systems the person named, which is the closest thing to a
# stated fact in the whole description.
BY_SYSTEM: tuple[tuple[tuple[str, ...], tuple[StepKind, ...], NodeChoice], ...] = (
    (("google sheet", "google sheets", "gsheet"), (StepKind.READ, StepKind.WRITE, StepKind.TRANSFORM), GOOGLE_SHEETS),
    (("slack",), (StepKind.NOTIFY, StepKind.WRITE), SLACK),
    (("gmail", "google mail"), (StepKind.READ, StepKind.WRITE, StepKind.NOTIFY), GMAIL),
)

# Weaker, so only used where the systems said nothing useful.
BY_CAPABILITY: tuple[tuple[tuple[str, ...], tuple[StepKind, ...], NodeChoice], ...] = (
    (("http", "rest api", "api call", "webhook call"), (StepKind.READ, StepKind.WRITE, StepKind.NOTIFY), HTTP),
    (("send email", "send an email", "email the", "reply to email"), (StepKind.NOTIFY,), SEND_EMAIL),
    (("calculate", "reformat", "reshape", "convert", "transform"), (StepKind.TRANSFORM,), CODE),
    (("map field", "set field", "build record"), (StepKind.TRANSFORM,), SET),
)


def _haystack(step: Step, graph: ProcessGraph, capability: str | None) -> str:
    """Everything we know about this step, lower case, in one string.

    The system name is included by looking up the step's system_id, because the
    graph stores systems separately and the useful words live in their names.
    """

    parts = [step.name, step.description, capability or ""]

    system = next((s for s in graph.systems if s.id == step.system_id), None)
    if system:
        parts.append(system.name)

    return " ".join(parts).lower()


def choose_node(step: Step, graph: ProcessGraph, capability: str | None) -> NodeChoice | None:
    """A real node for this step, or None to leave a placeholder.

    Returning None is a perfectly good answer and the common one. A placeholder
    that says what belongs there costs somebody one node to replace. A confident
    wrong node costs them the time to work out why it does not do what its name
    claims.
    """

    # A person doing something themselves has no node, and a branch already
    # became an IF before this was ever called.
    if step.kind in (StepKind.JUDGEMENT, StepKind.DECISION, StepKind.WAIT):
        return None

    text = _haystack(step, graph, capability)

    for needles, kinds, choice in BY_SYSTEM:
        if step.kind in kinds and any(n in text for n in needles):
            return choice

    for needles, kinds, choice in BY_CAPABILITY:
        if step.kind in kinds and any(n in text for n in needles):
            return choice

    return None
