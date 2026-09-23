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
#
# Judgement joined the other three after the live eval kept catching the same
# contradiction: a step flagged as needing judgement no rule covers, and then
# marked safe to run with nobody watching. If no rule covers it, a machine has
# nothing to follow, so somebody has to be there.
NEVER_FULLY_AUTOMATIC = frozenset(
    {
        RiskFlag.MOVES_MONEY,
        RiskFlag.IRREVERSIBLE,
        RiskFlag.LEGAL_OR_COMPLIANCE,
        RiskFlag.SUBJECTIVE_JUDGEMENT,
    }
)

# What each risk means to somebody reading the page, so that when we overrule the
# model we can say why in a sentence rather than showing them our enum. Written
# to slot after "it": "moves money", not "it moves money".
RISK_IN_PLAIN_ENGLISH: dict[RiskFlag, str] = {
    RiskFlag.MOVES_MONEY: "moves money",
    RiskFlag.IRREVERSIBLE: "cannot be undone",
    RiskFlag.LEGAL_OR_COMPLIANCE: "carries legal weight",
    RiskFlag.EXTERNAL_COMMS: "sends something outside the business",
    RiskFlag.PERSONAL_DATA: "handles personal data",
    RiskFlag.CUSTOMER_FACING: "produces something a client sees",
    RiskFlag.BULK_ACTION: "acts on many things at once",
    RiskFlag.SUBJECTIVE_JUDGEMENT: "calls for judgement no rule covers",
}


def in_plain_english(risks) -> str:
    """Risk flags as a readable list: "moves money and cannot be undone"."""

    parts = [RISK_IN_PLAIN_ENGLISH.get(r, str(r).replace("_", " ")) for r in risks]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]

# The rule above only helps if the risk was noticed in the first place, and that
# was being left entirely to the model. It marked "delete the email" as safe to
# run unattended, which it plainly is not.
#
# So the obvious cases are caught here instead, by looking at what the step says
# it does. Deliberately crude word matching: a safety net needs to be predictable
# and explainable more than it needs to be clever. "It flagged this because the
# step says 'delete'" is something a person can audit, argue with and correct. A
# model deciding case by case is none of those things.
#
# False positives are the acceptable direction of error here. Being asked to
# confirm a deletion that was actually safe costs somebody a click. Not being
# asked costs them the data.
RISK_WORDS: dict[RiskFlag, frozenset[str]] = {
    RiskFlag.IRREVERSIBLE: frozenset(
        {
            "delete", "deletes", "deleting", "deleted",
            "remove", "removes", "removing", "removed",
            "erase", "erases", "erasing", "erased",
            "purge", "purges", "purging", "purged",
            "wipe", "wipes", "wiping", "wiped",
            "destroy", "destroys", "destroying", "destroyed",
            "discard", "discards", "discarding", "discarded",
            "overwrite", "overwrites", "overwriting", "overwritten",
            "cancel", "cancels", "cancelling", "cancelled",
            "revoke", "revokes", "revoking", "revoked",
        }
    ),
    RiskFlag.MOVES_MONEY: frozenset(
        {
            "pay", "pays", "paying", "payment", "payments",
            "transfer", "transfers", "transferring",
            "refund", "refunds", "refunding",
            "charge", "charges", "charging",
            "remit", "remits", "remitting",
            "reimburse", "reimburses", "reimbursing",
            "withdraw", "withdraws", "withdrawing",
        }
    ),
}

# Recording that money moved is not moving money. "Mark as paid" writes a row in
# a spreadsheet; it does not touch a bank account. Without this, half a finance
# process lights up amber, and a warning on everything is a warning on nothing.
RECORDING_VERBS = frozenset(
    {"mark", "log", "record", "note", "update", "enter", "type", "tick",
     "copy", "add", "append", "file", "save", "store", "track"}
)

# "Send an email" is only externally facing if it leaves the organisation, so
# this one needs both halves to match. An internal Slack message is not the same
# risk as a message to a client.
SENDING_WORDS = frozenset(
    {"send", "sends", "sending", "sent", "email", "emails", "emailing",
     "emailed", "reply", "replies", "replying", "replied", "post", "posts",
     "publish", "publishes", "publishing", "published", "text", "texts"}
)
OUTSIDE_WORDS = frozenset(
    {"customer", "customers", "client", "clients", "supplier", "suppliers",
     "vendor", "vendors", "applicant", "applicants", "landlord", "landlords",
     "tenant", "tenants", "candidate", "candidates", "public", "buyer",
     "buyers", "seller", "sellers", "guest", "guests"}
)


# Three risks are in the never-fully-automatic rule and only two of them had any
# code behind them. Legal weight was left entirely to the model, which is exactly
# the arrangement that let "delete the email" through, so it gets the same
# treatment as the other two.
#
# On the leading verb only. Signing an agreement is an action with consequences;
# reading a signed one is not, and a rule that cannot tell them apart would put
# an amber light on half a lettings process.
LEGAL_VERBS = frozenset(
    {
        "sign", "signs", "signing", "signed",
        "countersign", "countersigns", "countersigning", "countersigned",
        "terminate", "terminates", "terminating", "terminated",
        "evict", "evicts", "evicting", "evicted",
        "serve", "serves", "serving", "served",
    }
)

# Some money is only visible in a pair of words. Writing off a balance, issuing
# a credit note and releasing a deposit are all money moving, and every single
# word in them is too common to match on: "balance" is in every reconciliation
# step, "credit" is in every credit check, and you release listings, keys and
# reports as happily as you release funds.
#
# So these are matched as phrases in order, which stays as explainable as the
# word lists above. "It fired because the step says credit note" is something a
# person can argue with. Order is what does the work: "note the credit check"
# contains both words and is not a credit note.
MONEY_PHRASES: tuple[tuple[str, ...], ...] = (
    ("write", "off"),
    ("writing", "off"),
    ("written", "off"),
    ("credit", "note"),
    ("debit", "note"),
    ("release", "deposit"),
    ("releasing", "deposit"),
    ("released", "deposit"),
    ("release", "fund"),
    ("releasing", "fund"),
    ("draw", "down"),
    ("drawing", "down"),
)

# Skipped when matching a phrase, so "release the deposit" and "release deposit"
# are the same thing.
FILLER_WORDS = frozenset(
    {"the", "a", "an", "our", "their", "its", "his", "her", "this", "that", "any"}
)

# One real word is allowed between the parts of a phrase, because an adjective
# in the middle does not change what the step does: "release the holding deposit"
# is still releasing a deposit.
#
# One rather than two, and that is the whole trade off. Two would let "write the
# inspection report off site" through as money. One lets an adjective in and
# keeps a clause out, which is the line worth drawing.
MAX_WORDS_BETWEEN = 1

# The live eval's misses, turned into rules. Each is written for the kind of
# step, not for the eval's wording, and has cases in both directions in
# evals/cases.py using different words from the ones that exposed it.

# Deciding on what somebody outside the business said. "Did the tenant agree to
# the deductions" and "did they say yes to renewing" read like routing, but the
# answer arrives as a person's words, and working out what they meant is the
# judgement. The process then acts on it: releases money, or puts somebody's
# home back on the market. Decision steps only, so reading the reply is not
# caught; deciding what it means is.
REPLY_WORDS = frozenset(
    {
        "said", "says", "say",
        "agree", "agrees", "agreed", "agreement",
        "disagree", "disagrees", "disagreed",
        "dispute", "disputes", "disputed",
        "accept", "accepts", "accepted",
        "reject", "rejects", "rejected",
        "decline", "declines", "declined",
        "reply", "replies", "replied",
        "response", "responds", "responded",
        "answer", "answers", "answered",
    }
)

# Judging the condition of something. What counts as damage rather than fair
# wear and tear is the question at the heart of every deposit dispute, and no
# rule settles it. Needs both an assessing verb in the lead and a condition word,
# so "log the damage the tenant reported" stays quiet: that records a report, it
# does not judge anything.
ASSESSING_VERBS = frozenset(
    {
        "compare", "compares", "comparing",
        "assess", "assesses", "assessing",
        "inspect", "inspects", "inspecting",
        "identify", "identifies", "identifying",
        "judge", "judges", "judging",
        "evaluate", "evaluates", "evaluating",
        "review", "reviews", "reviewing",
    }
)
CONDITION_WORDS = frozenset(
    {
        "damage", "damaged", "damages",
        "wear", "condition",
        "defect", "defects", "defective",
        "fault", "faults", "faulty",
    }
)

# Taking money off what somebody is owed is always governed by something: a
# deposit protection scheme, tax law, or a contract. Deductions from a tenancy
# deposit can be disputed and go to an adjudicator, so deciding or writing them
# up carries legal weight. Recording deductions already agreed does not.
DEDUCTION_WORDS = frozenset(
    {"deduct", "deducts", "deducting", "deducted", "deduction", "deductions"}
)


def _singular(word: str) -> str:
    """Crudest possible plural handling, so "credit notes" matches "credit note".

    Not linguistics. Just enough that a phrase list does not need two entries
    for every noun in it.
    """

    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _has_phrase(ordered: list[str], phrase: tuple[str, ...]) -> bool:
    """Does this phrase appear in order, close together, in this text?

    Filler is ignored entirely and one real word is allowed between the parts.
    Order is the important half: "note the credit check" contains both words of
    "credit note" and means something completely different.
    """

    wanted = [_singular(w) for w in phrase]
    position = 0
    skipped = 0

    for token in ordered:
        if token in FILLER_WORDS:
            continue

        word = _singular(token)
        if word == wanted[position]:
            position += 1
            skipped = 0
            if position == len(wanted):
                return True
            continue

        if position and skipped < MAX_WORDS_BETWEEN:
            skipped += 1
            continue

        # Too far from the last part to still be the same phrase. Start again,
        # letting this token be a fresh first word.
        position = 1 if word == wanted[0] else 0
        skipped = 0

    return False


def words_in(text: str) -> list[str]:
    """Lower case words in order, punctuation and possessives stripped.

    "The tenant's reply" has to find "tenant", or every word list here quietly
    misses the possessive, which is how people write. Shared with the question
    rules, so both read a step the same way.
    """

    words = []
    for raw in text.split():
        word = raw.strip(".,;:!?()'\"").lower()
        for possessive in ("'s", "’s"):
            if word.endswith(possessive):
                word = word[: -len(possessive)]
                break
        words.append(word)
    return words


def mandatory_risks(name: str, description: str = "", kind: str | None = None) -> set[RiskFlag]:
    """Risks the step carries by virtue of what it does, whatever the model said.

    Some checks look at the whole text and some only at the leading verb, and the
    difference matters. "Delete" anywhere means something is being destroyed. But
    "paid" anywhere catches "mark as paid", which touches a spreadsheet and not a
    bank account, so money and messaging are judged on what the step is actually
    doing rather than on what it mentions.
    """

    ordered = words_in(f"{name} {description}")
    words = set(ordered)
    lead = next(iter(name.split()), "").strip(".,;:!?()'\"").lower()

    found: set[RiskFlag] = set()

    # Destroying something is destroying it wherever the word appears.
    if words & RISK_WORDS[RiskFlag.IRREVERSIBLE]:
        found.add(RiskFlag.IRREVERSIBLE)

    # Moving money, but not merely writing down that it moved. The phrases get
    # the same leading-verb test as the single words: logging that a credit note
    # exists is not issuing one.
    moves_money = bool(words & RISK_WORDS[RiskFlag.MOVES_MONEY]) or any(
        _has_phrase(ordered, phrase) for phrase in MONEY_PHRASES
    )
    if moves_money and lead not in RECORDING_VERBS:
        found.add(RiskFlag.MOVES_MONEY)

    # Sending has to be the action, not something the step happens to mention.
    # Reading an email from a supplier is not communicating with them.
    sending = kind == "notify" or lead in SENDING_WORDS
    if sending and words & OUTSIDE_WORDS:
        found.add(RiskFlag.EXTERNAL_COMMS)

    # Binding somebody to something, or ending it. Same leading-verb test.
    if lead in LEGAL_VERBS:
        found.add(RiskFlag.LEGAL_OR_COMPLIANCE)

    # Deciding or writing up deductions, but not recording ones already agreed.
    if words & DEDUCTION_WORDS and lead not in RECORDING_VERBS:
        found.add(RiskFlag.LEGAL_OR_COMPLIANCE)

    # The map itself said a person decides this. The model's own labels are
    # trusted when they add caution and never when they remove it, so this is
    # taken at its word, while a step labelled "read" is still checked.
    if kind == "judgement":
        found.add(RiskFlag.SUBJECTIVE_JUDGEMENT)

    # A decision that turns on what somebody outside the business said.
    if kind == "decision" and words & REPLY_WORDS and words & OUTSIDE_WORDS:
        found.add(RiskFlag.SUBJECTIVE_JUDGEMENT)

    # Judging the condition of something, whatever kind the step was given.
    if lead in ASSESSING_VERBS and words & CONDITION_WORDS:
        found.add(RiskFlag.SUBJECTIVE_JUDGEMENT)

    return found


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


class OverrideKind(StrEnum):
    """Which house rule corrected the model."""

    RISK_ADDED = "risk_added"                    # we spotted what it missed
    VERDICT_DOWNGRADED = "verdict_downgraded"    # its verdict contradicted the risks
    CONTROL_ADDED = "control_added"              # it asked for a guard and gave none


class Override(Base):
    """One record of the code disagreeing with the model.

    This is the most interesting thing the system does and it used to leave
    almost no trace: a sentence appended to the rationale for one of the three
    rules, and nothing at all for the other two. Somebody reading a guarded step
    could not tell whether the model had been sensible or had been caught.

    Written only by `_normalise` in assess.py. Anything a model puts here is
    thrown away, because the record of a model being corrected is not the
    model's to write.
    """

    kind: OverrideKind
    was: str = Field(description="What the model produced.")
    now: str = Field(description="What it became.")
    because: str = Field(description="Why, in one sentence addressed to the reader.")


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
    overrides: list[Override] = Field(
        default_factory=list,
        description="Filled in by us after you answer. Leave it out.",
    )

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
