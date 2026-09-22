"""Turn a process graph and its automation plan into an n8n workflow file.

What this produces is a scaffold, not a running automation, and it says so on
every node. Guessing that someone means Gmail rather than Outlook or IMAP, and
inventing the credentials and field mappings to go with it, would produce a file
that imports and then fails in ways that are tedious to unpick. A skeleton with
the right shape and honest placeholders is more useful than a confident wrong
answer.

What it does get right, and what makes it worth exporting at all:

  - the shape. Steps, branches and the order they run in.
  - decisions become real IF or Switch nodes, not comments.
  - approvals become real Wait nodes, so the pause is in the workflow rather
    than in a paragraph somebody has to remember to read.
  - a threshold approval becomes an IF on the threshold, so "only above 5,000"
    is encoded rather than described. This is what the structured Threshold on
    a control is for.
"""

from __future__ import annotations

import json
from typing import Any

from app.n8n_catalogue import NodeChoice, choose_node
from app.schemas.assessment import AutomationPlan, ControlKind, StepAssessment
from app.schemas.common import ComparisonOperator, DataType
from app.schemas.process import ProcessGraph, Step, StepKind, TriggerKind

# n8n node types we emit. Only ones that exist and take no credentials, so the
# file always imports cleanly.
TRIGGER_TYPES: dict[TriggerKind, str] = {
    TriggerKind.SCHEDULE: "n8n-nodes-base.scheduleTrigger",
    TriggerKind.WEBHOOK: "n8n-nodes-base.webhook",
    TriggerKind.FORM_SUBMISSION: "n8n-nodes-base.formTrigger",
    TriggerKind.MANUAL: "n8n-nodes-base.manualTrigger",
    TriggerKind.INBOUND_MESSAGE: "n8n-nodes-base.scheduleTrigger",
    TriggerKind.FILE_ARRIVAL: "n8n-nodes-base.scheduleTrigger",
}

NO_OP = "n8n-nodes-base.noOp"
IF_NODE = "n8n-nodes-base.if"
SWITCH_NODE = "n8n-nodes-base.switch"
WAIT_NODE = "n8n-nodes-base.wait"

# What to tell someone to put in place of each placeholder.
REPLACE_WITH: dict[StepKind, str] = {
    StepKind.READ: "the node for the system this reads from, e.g. Gmail, IMAP, HTTP Request",
    StepKind.EXTRACT: "an extraction node, e.g. Extract from File, or an AI node for documents",
    StepKind.TRANSFORM: "a Code or Set node",
    StepKind.WRITE: "the node for the system this writes to, e.g. Google Sheets, Airtable, HTTP Request",
    StepKind.NOTIFY: "a messaging node, e.g. Slack, Send Email",
    StepKind.JUDGEMENT: "nothing. A person does this.",
    StepKind.WAIT: "a Wait node configured with the real delay",
    StepKind.DECISION: "nothing. This is already an IF or Switch.",
}

OPERATOR_MAP: dict[ComparisonOperator, str] = {
    ComparisonOperator.GT: "gt",
    ComparisonOperator.GTE: "gte",
    ComparisonOperator.LT: "lt",
    ComparisonOperator.LTE: "lte",
    ComparisonOperator.EQ: "equals",
    ComparisonOperator.NEQ: "notEquals",
    ComparisonOperator.CONTAINS: "contains",
    ComparisonOperator.NOT_CONTAINS: "notContains",
    ComparisonOperator.IS_EMPTY: "empty",
    ComparisonOperator.IS_NOT_EMPTY: "notEmpty",
}

COLUMN = 280
ROW = 180

# n8n shows node names in a small box, so long ones get unreadable. Trim at a
# word boundary rather than mid-word; the full text is in the note anyway.
NAME_LIMIT = 54

TRIGGER_NAMES: dict[TriggerKind, str] = {
    TriggerKind.SCHEDULE: "On a schedule",
    TriggerKind.WEBHOOK: "On a webhook call",
    TriggerKind.FORM_SUBMISSION: "On a form submission",
    TriggerKind.MANUAL: "Started by hand",
    TriggerKind.INBOUND_MESSAGE: "When a message arrives",
    TriggerKind.FILE_ARRIVAL: "When a file arrives",
}


def _shorten(text: str, limit: int = NAME_LIMIT) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return f"{cut or text[: limit - 1]}..."


class _Builder:
    """Accumulates nodes and connections, keyed by the display name n8n uses."""

    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []
        self.connections: dict[str, dict[str, list[list[dict[str, Any]]]]] = {}
        self._names: set[str] = set()
        self._depth = 0

    def add(
        self,
        name: str,
        node_type: str,
        *,
        parameters: dict | None = None,
        notes: str | None = None,
        type_version: int = 1,
        outputs: int = 1,
    ) -> str:
        """Add a node and return the unique name it ended up with."""

        unique = name
        suffix = 2
        while unique in self._names:
            unique = f"{name} ({suffix})"
            suffix += 1
        self._names.add(unique)

        node: dict[str, Any] = {
            "parameters": parameters or {},
            "id": unique.lower().replace(" ", "-")[:60],
            "name": unique,
            "type": node_type,
            "typeVersion": type_version,
            "position": [COLUMN, self._depth * ROW],
        }
        if notes:
            node["notes"] = notes
            node["notesInFlow"] = True

        self.nodes.append(node)
        self.connections.setdefault(unique, {"main": [[] for _ in range(outputs)]})
        self._depth += 1
        return unique

    def connect(self, source: str, target: str, output: int = 0) -> None:
        main = self.connections.setdefault(source, {"main": [[]]})["main"]
        while len(main) <= output:
            main.append([])
        main[output].append({"node": target, "type": "main", "index": 0})


def to_n8n(graph: ProcessGraph, plan: AutomationPlan | None = None) -> dict:
    """Build an importable n8n workflow from a process and its plan."""

    builder = _Builder()
    assessments = {a.step_id: a for a in (plan.assessments if plan else [])}

    trigger_name = builder.add(
        TRIGGER_NAMES.get(graph.trigger.kind, "Start"),
        TRIGGER_TYPES.get(graph.trigger.kind, "n8n-nodes-base.manualTrigger"),
        parameters=_trigger_parameters(graph),
        notes=_trigger_notes(graph),
    )

    # Each step becomes one or more nodes. entry_of is where an incoming edge
    # should land (an approval gate, if the step has one) and exit_of is what a
    # following edge should leave from.
    entry_of: dict[str, str] = {}
    exit_of: dict[str, str] = {}

    for step in graph.steps:
        assessment = assessments.get(step.id)
        entry, exit_ = _build_step(builder, step, assessment, graph)
        entry_of[step.id] = entry
        exit_of[step.id] = exit_

    builder.connect(trigger_name, entry_of[graph.trigger.first_step_id])

    for step in graph.steps:
        outgoing = graph.outgoing(step.id)
        for index, edge in enumerate(outgoing):
            output = index if step.kind is StepKind.DECISION else 0
            builder.connect(exit_of[step.id], entry_of[edge.to_step], output)

    return {
        "name": graph.title,
        "nodes": builder.nodes,
        "connections": builder.connections,
        "settings": {"executionOrder": "v1"},
        "meta": {
            "description": (
                f"{graph.summary} Generated as a scaffold by the AI Automation "
                "Architect. Placeholder nodes need replacing with real integrations "
                "before this will run; each one says what it is waiting for."
            )
        },
        "tags": [],
    }


def _build_step(
    builder: _Builder, step: Step, assessment: StepAssessment | None, graph: ProcessGraph
) -> tuple[str, str]:
    """Return (entry node, exit node) for one step, inserting any approval gate."""

    gate_entry: str | None = None
    gate_exit: str | None = None

    for control in assessment.controls if assessment else []:
        if control.kind is ControlKind.THRESHOLD_APPROVAL and control.threshold:
            # Encode the limit as a real branch. Above it, wait for a person;
            # below it, carry straight on.
            check = builder.add(
                _shorten(f"Over the limit? {step.name}"),
                IF_NODE,
                parameters=_threshold_parameters(control.threshold),
                notes=control.reason,
                type_version=2,
                outputs=2,
            )
            wait = builder.add(
                _shorten(f"Wait for approval: {step.name}"),
                WAIT_NODE,
                parameters={"resume": "webhook"},
                notes=_approval_notes(control.who_approves, control.reason),
                type_version=1,
            )
            builder.connect(check, wait, 0)
            gate_entry, gate_exit = check, wait
            break

        if control.kind in (ControlKind.HUMAN_APPROVAL, ControlKind.DRY_RUN_FIRST):
            wait = builder.add(
                _shorten(f"Wait for approval: {step.name}"),
                WAIT_NODE,
                parameters={"resume": "webhook"},
                notes=_approval_notes(control.who_approves, control.reason),
                type_version=1,
            )
            gate_entry = gate_exit = wait
            break

    if step.kind is StepKind.DECISION:
        body = builder.add(
            _shorten(step.name),
            IF_NODE,
            parameters={},
            notes=_step_notes(step, assessment),
            type_version=2,
            outputs=2,
        )
    else:
        # A real node where the description named a system we recognise, a
        # placeholder otherwise. See n8n_catalogue for why that line is drawn
        # from what the person said rather than from what the model suggested.
        capability = assessment.tool.capability if assessment and assessment.tool else None
        choice = choose_node(step, graph, capability)

        body = builder.add(
            _shorten(step.name),
            choice.type if choice else NO_OP,
            notes=_step_notes(step, assessment, choice),
            type_version=choice.version if choice else 1,
        )

    if gate_entry and gate_exit:
        builder.connect(gate_exit, body)
        # The below-threshold branch skips the wait and goes straight through.
        if builder.connections[gate_entry]["main"] and gate_entry != gate_exit:
            builder.connect(gate_entry, body, 1)
        return gate_entry, body

    return body, body


def _trigger_parameters(graph: ProcessGraph) -> dict:
    if graph.trigger.kind is TriggerKind.SCHEDULE:
        return {"rule": {"interval": [{"field": "days", "daysInterval": 1}]}}
    if graph.trigger.kind is TriggerKind.WEBHOOK:
        return {"path": graph.trigger.first_step_id, "httpMethod": "POST"}
    return {}


def _trigger_notes(graph: ProcessGraph) -> str:
    note = f"{graph.trigger.description}"
    if graph.trigger.schedule_hint:
        note += f"\nTiming as described: {graph.trigger.schedule_hint}. Set the real schedule here."
    if graph.trigger.kind in (TriggerKind.INBOUND_MESSAGE, TriggerKind.FILE_ARRIVAL):
        note += (
            "\nThis was described as happening when something arrives. A polling "
            "schedule is used as a stand-in. Replace it with the matching trigger "
            "for that system if it has one."
        )
    return note


def _step_notes(
    step: Step, assessment: StepAssessment | None, choice: NodeChoice | None = None
) -> str:
    lines = [step.description]

    if step.iterates_over:
        lines.append(f"Repeats for: {step.iterates_over}. Add a Split In Batches node if needed.")
    if step.assumption:
        lines.append(f"Assumed: {step.assumption}")

    if assessment:
        lines.append(f"Verdict: {assessment.verdict.value.replace('_', ' ')}.")
        lines.append(assessment.rationale)
        if assessment.risks:
            lines.append("Risks: " + ", ".join(r.value.replace("_", " ") for r in assessment.risks))
        for blocker in assessment.blockers:
            lines.append(f"Blocked by {blocker.kind.value.replace('_', ' ')}: {blocker.detail}")

    replace = REPLACE_WITH.get(step.kind)
    if replace:
        lines.append(f"REPLACE THIS with {replace}")

    return "\n".join(line for line in lines if line)


def _approval_notes(who: str | None, reason: str) -> str:
    lines = [
        reason,
        "This pauses until the resume webhook is called. Wire that up to however "
        "approval actually happens: a Slack button, an email link, a form.",
    ]
    if who:
        lines.append(f"Approver: {who}")
    return "\n".join(lines)


def _threshold_parameters(threshold) -> dict:
    """An n8n v2 IF node condition built from our structured threshold."""

    numeric = threshold.value_type in (DataType.NUMBER, DataType.MONEY)
    operator = OPERATOR_MAP.get(threshold.operator, "equals")

    return {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "version": 2},
            "conditions": [
                {
                    "id": "threshold",
                    "leftValue": f"={{{{ $json.{threshold.field} }}}}",
                    "rightValue": (
                        float(threshold.value)
                        if numeric and _is_number(threshold.value)
                        else (threshold.value or "")
                    ),
                    "operator": {
                        "type": "number" if numeric else "string",
                        "operation": operator,
                    },
                }
            ],
            "combinator": "and",
        },
        "options": {},
    }


def _is_number(value: str | None) -> bool:
    try:
        float(value or "")
    except (TypeError, ValueError):
        return False
    return True


def to_n8n_json(graph: ProcessGraph, plan: AutomationPlan | None = None) -> str:
    return json.dumps(to_n8n(graph, plan), indent=2)
