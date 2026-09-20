"""Stage two: the judgement layer.

process.py says what happens today. This says what should be automated, what
must stay with a person, and what guard rails belong on the bits in between.

The opinions in this file are the product. A flowchart is a commodity; knowing
that nobody should let a machine pay an invoice unattended is not.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from app.schemas.common import Base, Slug, Threshold
from app.schemas.process import ProcessGraph


class Confidence(StrEnum):
    """Three buckets, deliberately.

    A model will happily tell you it is 0.87 confident. That number is invented
    and the false precision makes the whole output less trustworthy, not more.
    High / medium / low is the most we can honestly claim.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Verdict(StrEnum):
    FULLY_AUTOMATABLE = "fully_automatable"
    AUTOMATABLE_WITH_CONTROL = "automatable_with_control"
    HUMAN_REQUIRED = "human_required"
    NEEDS_MORE_INFO = "needs_more_info"


class RiskFlag(StrEnum):
    """Ways an unattended step can hurt someone."""

    MOVES_MONEY = "moves_money"                    # pays, refunds, transfers
    IRREVERSIBLE = "irreversible"                  # cannot be undone once done
    LEGAL_OR_COMPLIANCE = "legal_or_compliance"    # contracts, AML, regulated advice
    EXTERNAL_COMMS = "external_comms"              # leaves the organisation
    PERSONAL_DATA = "personal_data"                # identifies a living person
    CUSTOMER_FACING = "customer_facing"            # a client sees the output
    BULK_ACTION = "bulk_action"                    # one mistake, multiplied
    SUBJECTIVE_JUDGEMENT = "subjective_judgement"  # no rule covers it


# Our house rule. If a step carries one of these, we will not call it fully
# automatable no matter how confident the model feels. Encoding the policy here
# rather than in a prompt means it cannot be talked out of it.
NEVER_FULLY_AUTOMATIC = frozenset(
    {RiskFlag.MOVES_MONEY, RiskFlag.IRREVERSIBLE, RiskFlag.LEGAL_OR_COMPLIANCE}
)


class ControlKind(StrEnum):
    """What we do about a risk."""

    HUMAN_APPROVAL = "human_approval"            # always ask before acting
    THRESHOLD_APPROVAL = "threshold_approval"    # ask only above a limit
    NOTIFY_AFTER = "notify_after"                # let it run, tell someone
    LOG_FOR_AUDIT = "log_for_audit"              # keep a record
    RATE_LIMIT = "rate_limit"                    # cap how many per run
    DRY_RUN_FIRST = "dry_run_first"              # preview before the first real run


class Control(Base):
    """A guard rail on a step."""

    kind: ControlKind
    reason: str = Field(description="Why this guard is here, in one sentence a manager would accept.")
    addresses: list[RiskFlag] = Field(
        default_factory=list, description="Which risks this control is answering."
    )
    threshold: Threshold | None = Field(
        default=None,
        description="Required for threshold_approval. The limit above which a person is asked.",
    )
    who_approves: str | None = Field(
        default=None, description="Role rather than a name, e.g. 'whoever owns the purchase ledger'."
    )

    @model_validator(mode="after")
    def _threshold_needs_a_limit(self) -> Control:
        if self.kind is ControlKind.THRESHOLD_APPROVAL and self.threshold is None:
            raise ValueError(
                "A threshold_approval control needs a threshold saying where the limit is."
            )
        if self.kind is not ControlKind.THRESHOLD_APPROVAL and self.threshold is not None:
            raise ValueError(
                f"A {self.kind} control should not carry a threshold; use threshold_approval."
            )
        return self


class BlockerKind(StrEnum):
    """Why a step resists automation. Practical obstacles, not risks."""

    NO_API = "no_api"                          # the system offers no way in
    PAPER_OR_PHYSICAL = "paper_or_physical"    # happens off a screen
    UNSTRUCTURED_INPUT = "unstructured_input"  # varies too much to parse reliably
    NEEDS_CREDENTIALS = "needs_credentials"    # access we would have to be granted
    UNCLEAR_RULE = "unclear_rule"              # the person could not state the rule
    LOW_VOLUME = "low_volume"                  # too rare to be worth automating


class Blocker(Base):
    kind: BlockerKind
    detail: str = Field(description="What specifically is in the way.")
    workaround: str | None = Field(
        default=None, description="A realistic way round it, if there is one."
    )


class ToolMatch(Base):
    """What could actually perform this step."""

    capability: str = Field(description="The generic capability needed, e.g. 'read new email'.")
    candidates: list[str] = Field(
        default_factory=list,
        description="Concrete options, most suitable first, e.g. ['Gmail trigger', 'IMAP polling'].",
    )
    confidence: Confidence = Confidence.MEDIUM


class StepAssessment(Base):
    """The verdict on one step of the process."""

    step_id: Slug
    verdict: Verdict
    rationale: str = Field(description="Plain English, addressed to the person who does this work.")
    confidence: Confidence
    risks: list[RiskFlag] = Field(default_factory=list)
    controls: list[Control] = Field(default_factory=list)
    blockers: list[Blocker] = Field(default_factory=list)
    tool: ToolMatch | None = None

    @model_validator(mode="after")
    def _verdict_must_match_its_evidence(self) -> StepAssessment:
        errors: list[str] = []

        # Every message below says what to do, not just what is wrong. These go
        # back to a model as a repair instruction, and "that is invalid" with no
        # route out produces the same invalid answer again.

        dangerous = sorted(r.value for r in self.risks if r in NEVER_FULLY_AUTOMATIC)
        if dangerous and self.verdict is Verdict.FULLY_AUTOMATABLE:
            errors.append(
                f"Step '{self.step_id}' is marked fully_automatable but carries "
                f"{', '.join(dangerous)}. Change the verdict to "
                "automatable_with_control and add a control, or to human_required "
                "if a person must do it themselves."
            )

        if self.verdict is Verdict.AUTOMATABLE_WITH_CONTROL and not self.controls:
            errors.append(
                f"Step '{self.step_id}' is automatable_with_control but lists no "
                "control. Either add one (human_approval, threshold_approval with "
                "a threshold, notify_after, log_for_audit, rate_limit or "
                "dry_run_first), or change the verdict: use human_required if a "
                "person must carry the step out, or fully_automatable if nothing "
                "actually needs guarding."
            )

        if self.verdict is Verdict.FULLY_AUTOMATABLE and self.blockers:
            errors.append(
                f"Step '{self.step_id}' is marked fully_automatable but lists "
                f"{len(self.blockers)} blocker(s). Either remove the blockers, or "
                "change the verdict to needs_more_info."
            )

        if self.verdict is Verdict.NEEDS_MORE_INFO and not self.blockers:
            errors.append(
                f"Step '{self.step_id}' is needs_more_info but does not say what is "
                "missing. Add a blocker naming the obstacle, or choose a different verdict."
            )

        if errors:
            raise ValueError("\n".join(errors))
        return self

    @property
    def needs_a_person(self) -> bool:
        return self.verdict in (Verdict.HUMAN_REQUIRED, Verdict.AUTOMATABLE_WITH_CONTROL)


class AutomationPlan(Base):
    """The judgement layer over a whole process."""

    process_title: str
    assessments: list[StepAssessment] = Field(min_length=1)
    headline: str = Field(
        description="One or two sentences: the honest summary, including what will not be automated."
    )
    biggest_win: Slug | None = Field(
        default=None, description="The step worth doing first, if one stands out."
    )

    def for_step(self, step_id: str) -> StepAssessment | None:
        return next((a for a in self.assessments if a.step_id == step_id), None)

    def counts(self) -> dict[Verdict, int]:
        return {v: sum(1 for a in self.assessments if a.verdict is v) for v in Verdict}


def validate_plan(plan: AutomationPlan, graph: ProcessGraph) -> list[str]:
    """Check a plan against the process it claims to describe.

    Separate from the model validator above because this needs both objects, and
    a Pydantic model can only validate itself. Same pattern as validate_graph:
    return everything that is wrong so a repair can fix it in one pass.
    """

    errors: list[str] = []

    graph_ids = {s.id for s in graph.steps}
    assessed: set[str] = set()

    for assessment in plan.assessments:
        if assessment.step_id not in graph_ids:
            errors.append(
                f"Plan assesses step '{assessment.step_id}', which is not in the process."
            )
        if assessment.step_id in assessed:
            errors.append(f"Step '{assessment.step_id}' is assessed more than once.")
        assessed.add(assessment.step_id)

    for missing in sorted(graph_ids - assessed):
        errors.append(f"Step '{missing}' has no assessment.")

    if plan.biggest_win and plan.biggest_win not in graph_ids:
        errors.append(
            f"biggest_win points at '{plan.biggest_win}', which is not a step in the process."
        )

    # A step the person already does by hand, because it needs their judgement,
    # should not come back as something a machine can simply take over.
    for assessment in plan.assessments:
        step = graph.step(assessment.step_id)
        if step is None:
            continue
        if step.kind.value == "judgement" and assessment.verdict is Verdict.FULLY_AUTOMATABLE:
            errors.append(
                f"Step '{step.id}' was described as human judgement but is marked "
                "fully automatable. Say what rule replaces the judgement, or lower the verdict."
            )

    return errors
