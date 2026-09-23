"""The as-is process graph: what the person currently does, before any judgement.

This is stage one of two. Nothing here decides what should be automated. That
lives in assessment.py. Keeping description and judgement apart means each model
call has exactly one job, and it means we can validate the graph is structurally
sound before spending anything assessing it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from app.schemas.common import Base, DataItem, Slug, System, Threshold


class StepKind(StrEnum):
    """What a step does. Drives both the risk layer and the tool matching."""

    READ = "read"            # get information out of a system
    EXTRACT = "extract"      # pull structured data out of unstructured content
    TRANSFORM = "transform"  # reshape, calculate, reformat
    DECISION = "decision"    # the process branches here
    WRITE = "write"          # create or change data in a system
    NOTIFY = "notify"        # tell a person or a channel
    JUDGEMENT = "judgement"  # a human decides something that is not a simple rule
    WAIT = "wait"            # wait for time to pass or something external to happen


class TriggerKind(StrEnum):
    SCHEDULE = "schedule"
    INBOUND_MESSAGE = "inbound_message"
    FILE_ARRIVAL = "file_arrival"
    FORM_SUBMISSION = "form_submission"
    WEBHOOK = "webhook"
    MANUAL = "manual"


class Trigger(Base):
    """What starts the process off."""

    kind: TriggerKind
    description: str = Field(
        description="In the person's own words, e.g. 'every morning when I get in'."
    )
    system_id: Slug | None = None
    schedule_hint: str | None = Field(
        default=None,
        description="Plain English timing if kind is schedule, e.g. 'every weekday at 9am'.",
    )
    first_step_id: Slug = Field(description="The step the process begins with.")


class Step(Base):
    """One unit of work in the process."""

    id: Slug
    name: str = Field(
        description="Short imperative label, e.g. 'Download the PDF attachment'."
    )
    description: str
    kind: StepKind
    system_id: Slug | None = Field(
        default=None, description="Where this happens, if anywhere specific."
    )
    inputs: list[DataItem] = Field(default_factory=list)
    outputs: list[DataItem] = Field(default_factory=list)

    # Real processes repeat things: "for each invoice in the inbox". Modelling
    # that as a loop edge would make the graph cyclic, and a cyclic graph breaks
    # layout, topological ordering and export. So repetition is a property of a
    # step instead, and the graph stays a DAG.
    iterates_over: str | None = Field(
        default=None,
        description="Set when this step repeats once per item, e.g. 'each attachment on the email'.",
    )

    assumption: str | None = Field(
        default=None,
        description=(
            "Anything you had to assume because the description did not say. "
            "Raise a clarifying question as well."
        ),
    )


class Edge(Base):
    """A directed link between two steps."""

    from_step: Slug
    to_step: Slug

    # The prose condition is required on every edge leaving a decision, so a
    # branch can never be silently unlabelled. The structured form is
    # best-effort: when the condition is machine-checkable we capture it
    # properly, because that is what becomes a real branch node on export
    # rather than a comment.
    condition: str | None = Field(
        default=None,
        description=(
            "Required on edges out of a decision step, e.g. 'amount is over 5000'. "
            "Null otherwise."
        ),
    )
    condition_test: Threshold | None = Field(
        default=None,
        description=(
            "The same condition expressed machine-readably, when it can be. "
            "Null if it needs human judgement."
        ),
    )


class ClarifyingQuestion(Base):
    """Something the description did not say that materially changes the design."""

    id: Slug
    question: str = Field(description="Ask it plainly, as a colleague would.")
    why_it_matters: str = Field(description="What changes depending on the answer.")
    affects_step_ids: list[Slug] = Field(default_factory=list)
    suggested_answers: list[str] = Field(
        default_factory=list,
        description="Two to four likely answers so it can be answered in one click.",
    )


class Answer(Base):
    """What the person said when asked one of the questions above.

    The question text is carried rather than its id, because ids are generated
    fresh on every run and an answer has to outlive the analysis that prompted
    it. This is the one piece of input that did not come from the description,
    and it is treated as fact: the model is told to build it in, not to weigh it.
    """

    question: str = Field(max_length=500)
    answer: str = Field(min_length=1, max_length=500)


class ProcessGraph(Base):
    """A complete description of how the work is done today."""

    title: str = Field(description="Short name for the process, e.g. 'Invoice intake'.")
    summary: str = Field(description="One or two sentences a busy person could read.")
    trigger: Trigger
    systems: list[System] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)
    edges: list[Edge] = Field(default_factory=list)
    questions: list[ClarifyingQuestion] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_graph(self) -> ProcessGraph:
        errors = validate_graph(self)
        if errors:
            raise ValueError(
                "Invalid process graph:\n" + "\n".join(f"  - {e}" for e in errors)
            )
        return self

    # Convenience lookups, used by the assessment stage and the exporters.
    def step(self, step_id: str) -> Step | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def outgoing(self, step_id: str) -> list[Edge]:
        return [e for e in self.edges if e.from_step == step_id]

    def terminal_step_ids(self) -> list[str]:
        return [s.id for s in self.steps if not self.outgoing(s.id)]


def validate_graph(graph: ProcessGraph) -> list[str]:
    """Return every structural problem with the graph, as readable sentences.

    Deliberately returns a list rather than raising on the first problem. When a
    model produces a bad graph we want to hand back everything that is wrong in
    one go. A repair loop that fixes one fault per round trip is slow, and it
    tends to oscillate between two faults it keeps reintroducing.
    """

    errors: list[str] = []

    step_ids = [s.id for s in graph.steps]
    seen: set[str] = set()
    for sid in step_ids:
        if sid in seen:
            errors.append(f"Duplicate step id '{sid}'.")
        seen.add(sid)

    system_ids = {s.id for s in graph.systems}
    if len(system_ids) != len(graph.systems):
        errors.append("Two systems share the same id.")

    for step in graph.steps:
        if step.system_id and step.system_id not in system_ids:
            errors.append(
                f"Step '{step.id}' refers to system '{step.system_id}', which is not declared."
            )

    if graph.trigger.system_id and graph.trigger.system_id not in system_ids:
        errors.append(
            f"Trigger refers to system '{graph.trigger.system_id}', which is not declared."
        )

    # Edge endpoints must exist before anything downstream can be trusted.
    valid_edges: list[Edge] = []
    for edge in graph.edges:
        ok = True
        if edge.from_step not in seen:
            errors.append(f"Edge points from '{edge.from_step}', which is not a step.")
            ok = False
        if edge.to_step not in seen:
            errors.append(f"Edge points to '{edge.to_step}', which is not a step.")
            ok = False
        if ok and edge.from_step == edge.to_step:
            errors.append(f"Step '{edge.from_step}' links to itself.")
            ok = False
        if ok:
            valid_edges.append(edge)

    # The branching rules. This is the bit that stops a model flattening a
    # decision into a straight line, which is its strong natural tendency.
    for step in graph.steps:
        out = [e for e in valid_edges if e.from_step == step.id]
        if step.kind is StepKind.DECISION:
            if len(out) < 2:
                errors.append(
                    f"Decision step '{step.id}' has {len(out)} outgoing edge(s); "
                    "a decision needs at least two."
                )
            unlabelled = [e for e in out if not e.condition]
            if unlabelled:
                errors.append(
                    f"Decision step '{step.id}' has {len(unlabelled)} outgoing "
                    "edge(s) with no condition."
                )
        else:
            if len(out) > 1:
                errors.append(
                    f"Step '{step.id}' is kind '{step.kind}' but has {len(out)} "
                    "outgoing edges. Only a decision step may branch."
                )
            if any(e.condition for e in out):
                errors.append(
                    f"Step '{step.id}' is kind '{step.kind}' but its outgoing edge "
                    "carries a condition. Conditions belong on edges out of a decision step."
                )

    if graph.trigger.first_step_id not in seen:
        errors.append(
            f"Trigger starts at '{graph.trigger.first_step_id}', which is not a step."
        )
        return errors  # Reachability is meaningless without a valid entry point.

    adjacency: dict[str, list[str]] = {sid: [] for sid in seen}
    for edge in valid_edges:
        adjacency[edge.from_step].append(edge.to_step)

    cycle = _find_cycle(adjacency, graph.trigger.first_step_id)
    if cycle:
        errors.append(
            "The process loops back on itself: "
            + " -> ".join(cycle)
            + ". Use a step's iterates_over field for repetition instead of a loop."
        )
        return errors  # The reachability check below assumes no cycles.

    reachable = _reachable_from(adjacency, graph.trigger.first_step_id)
    for sid in sorted(seen - reachable):
        errors.append(f"Step '{sid}' cannot be reached from the start of the process.")

    if all(adjacency[sid] for sid in seen):
        errors.append("The process never ends: every step leads somewhere else.")

    for question in graph.questions:
        for sid in question.affects_step_ids:
            if sid not in seen:
                errors.append(
                    f"Question '{question.id}' refers to step '{sid}', which does not exist."
                )

    return errors


def _find_cycle(adjacency: dict[str, list[str]], start: str) -> list[str] | None:
    """Depth-first cycle detection that returns the offending path, not just a bool.

    The path is worth the extra few lines: 'a -> b -> c -> a' tells both a human
    and a repair prompt exactly what to fix.
    """

    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(adjacency, WHITE)
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        colour[node] = GREY
        path.append(node)
        for nxt in adjacency.get(node, []):
            if colour.get(nxt) == GREY:
                return path[path.index(nxt) :] + [nxt]
            if colour.get(nxt) == WHITE:
                found = visit(nxt)
                if found:
                    return found
        path.pop()
        colour[node] = BLACK
        return None

    for node in [start, *adjacency]:
        if colour.get(node) == WHITE:
            found = visit(node)
            if found:
                return found
    return None


def _reachable_from(adjacency: dict[str, list[str]], start: str) -> set[str]:
    seen = {start}
    stack = [start]
    while stack:
        node = stack.pop()
        for nxt in adjacency.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen
