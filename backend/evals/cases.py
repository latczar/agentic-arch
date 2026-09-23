"""The cases themselves, as data rather than as code.

Two kinds, because there are two different things worth measuring and they have
very different costs.

GuardCase covers our own safety net: given the label on a step, does the code
flag the right risks? No model involved, so these are free, instant and identical
every time. They are also where the interesting tension lives, because a safety
check has two ways to be wrong and only one of them is obvious. Missing a real
risk is the dangerous failure. Flagging a harmless step is the failure that gets
the whole feature switched off for crying wolf, so both directions are listed
below and both are graded.

PipelineCase covers the whole thing end to end, from a description somebody
might actually type. These need a model, so they cost quota and they do not give
the same answer twice.

Everything here is invented. The processes are the shape of real estate agency
work without being anybody's actual work, and no real person, property or
company appears in any of it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.assessment import RiskFlag
from evals.checks import (
    Check,
    asks_a_question,
    every_step_assessed,
    flagged,
    has_a_decision,
    never_unattended,
    something_is_automatable,
    steps_between,
)


@dataclass(frozen=True)
class GuardCase:
    """One step label, and exactly which risks the code must find in it."""

    id: str
    step_name: str
    kind: str
    expect: frozenset[RiskFlag]
    why: str
    description: str = ""
    # Set when we already know this one fails and have decided not to fix it
    # yet. It still runs and still gets reported, but it does not break a build.
    #
    # The alternative is deleting the case, which makes the score look better
    # and makes the suite less honest. A known gap that is measured is a to-do
    # list. A known gap that is quietly removed is a surprise waiting for
    # somebody. If one of these ever starts passing, the run says so.
    known_gap: str = ""


# Steps where the net has to catch something. Each one is a way an unattended
# automation could do real damage to a real agency.
SHOULD_FIRE: list[GuardCase] = [
    GuardCase(
        id="deletes-an-email",
        step_name="Delete the original email",
        kind="write",
        expect=frozenset({RiskFlag.IRREVERSIBLE}),
        why="The bug that started all of this. It was passed as safe to automate.",
    ),
    GuardCase(
        id="pays-a-contractor",
        step_name="Pay the contractor invoice",
        kind="write",
        expect=frozenset({RiskFlag.MOVES_MONEY}),
        why="Money leaving the account with nobody watching is the worst case.",
    ),
    GuardCase(
        id="refunds-a-deposit",
        step_name="Refund the holding deposit to the applicant",
        kind="write",
        expect=frozenset({RiskFlag.MOVES_MONEY}),
        why="A refund is money moving, even though it is money going back.",
    ),
    GuardCase(
        id="cancels-a-standing-order",
        step_name="Cancel the standing order",
        kind="write",
        expect=frozenset({RiskFlag.IRREVERSIBLE}),
        why="Cancelling is not deleting, but it is just as hard to take back.",
    ),
    GuardCase(
        id="purges-old-records",
        step_name="Purge viewings older than two years",
        kind="write",
        expect=frozenset({RiskFlag.IRREVERSIBLE}),
        why="Bulk tidying is the most plausible way to lose data by accident.",
    ),
    GuardCase(
        id="emails-a-vendor",
        step_name="Email the vendor the viewing feedback",
        kind="notify",
        expect=frozenset({RiskFlag.EXTERNAL_COMMS}),
        why="Anything that leaves the office and reaches a client needs a look first.",
    ),
    GuardCase(
        id="tells-an-applicant",
        step_name="Let the applicant know the offer was accepted",
        kind="notify",
        expect=frozenset({RiskFlag.EXTERNAL_COMMS}),
        why=(
            "The verb here is not a sending word, so this only fires on the step "
            "being a notify. Worth pinning, because that is the half that is easy "
            "to break."
        ),
    ),
    GuardCase(
        id="transfers-rent",
        step_name="Transfer the rent to the landlord account",
        kind="write",
        expect=frozenset({RiskFlag.MOVES_MONEY}),
        why=(
            "Mentions a landlord but is not a message to one. Money only, and the "
            "absence of the comms flag is as much the point as the presence of "
            "the money one."
        ),
    ),
    GuardCase(
        id="signs-an-agreement",
        step_name="Sign the tenancy agreement on our behalf",
        kind="write",
        expect=frozenset({RiskFlag.LEGAL_OR_COMPLIANCE}),
        why="Binding a client to a contract unattended is the one that ends in court.",
    ),
    GuardCase(
        id="serves-notice",
        step_name="Serve notice on the tenant",
        kind="write",
        expect=frozenset({RiskFlag.LEGAL_OR_COMPLIANCE}),
        why="A notice served in error has a statutory clock attached to it.",
    ),
    GuardCase(
        id="terminates-a-tenancy",
        step_name="Terminate the tenancy agreement",
        kind="write",
        expect=frozenset({RiskFlag.LEGAL_OR_COMPLIANCE}),
        why="Ending an agreement is not something to discover after the fact.",
    ),
    GuardCase(
        id="writes-off-a-balance",
        step_name="Write off the outstanding balance",
        kind="write",
        expect=frozenset({RiskFlag.MOVES_MONEY}),
        why="Forgiving a debt is a financial decision, whatever it is called.",
    ),
    GuardCase(
        id="issues-a-credit-note",
        step_name="Issue a credit note to the landlord",
        kind="write",
        expect=frozenset({RiskFlag.MOVES_MONEY}),
        why="A credit note reduces what somebody owes, which is money moving.",
    ),
    GuardCase(
        id="releases-a-deposit",
        step_name="Release the deposit to the tenant",
        kind="write",
        expect=frozenset({RiskFlag.MOVES_MONEY}),
        why="Deposit money leaving the scheme, and it cannot easily be pulled back.",
    ),
    GuardCase(
        id="marks-a-record-as-deleted",
        step_name="Mark the record as deleted",
        kind="write",
        expect=frozenset({RiskFlag.IRREVERSIBLE}),
        why=(
            "A known false positive, kept deliberately. This writes a flag on a "
            "row and destroys nothing, but destruction is checked on the whole "
            "text rather than the verb, and loosening that to catch this would "
            "let real deletions through. Being asked to confirm costs a click. "
            "The case is here so the trade off is recorded rather than forgotten."
        ),
    ),
    # Added after the live eval kept catching judgement being waved through.
    # Worded differently from the eval cases on purpose: a rule that only
    # passes the sentence that exposed it has been fitted, not fixed.
    GuardCase(
        id="decides-on-a-vendors-answer",
        step_name="Work out whether the vendor has accepted the offer",
        kind="decision",
        expect=frozenset({RiskFlag.SUBJECTIVE_JUDGEMENT}),
        why="What a vendor meant by their reply is interpretation, and the sale moves on it.",
    ),
    GuardCase(
        id="decides-on-a-landlords-reply",
        step_name="Check the landlord's reply to see if they want the quote",
        kind="decision",
        expect=frozenset({RiskFlag.SUBJECTIVE_JUDGEMENT}),
        why=(
            "The same, with a possessive in it. \"landlord's\" used to slip past "
            "every word list, because that is how people actually write."
        ),
    ),
    GuardCase(
        id="judges-wear-and-tear",
        step_name="Assess whether the carpet stains are wear and tear",
        kind="read",
        expect=frozenset({RiskFlag.SUBJECTIVE_JUDGEMENT}),
        why=(
            "The question every deposit dispute turns on. Labelled a read here, "
            "because the rule has to hold whatever kind the model gave the step."
        ),
    ),
    GuardCase(
        id="a-step-the-map-calls-judgement",
        step_name="Decide whether the repair can wait until Monday",
        kind="judgement",
        expect=frozenset({RiskFlag.SUBJECTIVE_JUDGEMENT}),
        why="The map said a person decides this. Caution from the model is taken at its word.",
    ),
    GuardCase(
        id="drafts-deductions",
        step_name="Draft the list of deductions for the tenant",
        kind="write",
        expect=frozenset({RiskFlag.LEGAL_OR_COMPLIANCE}),
        why="A proposed deduction from a deposit is a claim the tenant can take to adjudication.",
    ),
]


# Steps where firing would be wrong. These are the ones that make the difference
# between a safety net people trust and an amber light on everything.
SHOULD_STAY_QUIET: list[GuardCase] = [
    GuardCase(
        id="marks-a-payment-received",
        step_name="Mark the payment as received",
        kind="write",
        expect=frozenset(),
        why="Writing down that money moved is not moving money.",
    ),
    GuardCase(
        id="logs-a-payment-reference",
        step_name="Log the payment reference in the ledger",
        kind="write",
        expect=frozenset(),
        why="Same again. Record keeping, not banking.",
    ),
    GuardCase(
        id="records-a-refund",
        step_name="Record the refund against the tenancy",
        kind="write",
        expect=frozenset(),
        why="The word refund is right there and it still must not fire.",
    ),
    GuardCase(
        id="copies-a-transfer-total",
        step_name="Copy the transfer total into the spreadsheet",
        kind="write",
        expect=frozenset(),
        why="Copying a number about a transfer is not making one.",
    ),
    GuardCase(
        id="updates-a-charge-amount",
        step_name="Update the sheet with the charge amount",
        kind="write",
        expect=frozenset(),
        why="Four money words in the suite that all belong to bookkeeping.",
    ),
    GuardCase(
        id="reads-a-supplier-email",
        step_name="Read the email from the supplier",
        kind="read",
        expect=frozenset(),
        why=(
            "Both halves of the comms rule are present as words, and it still "
            "must stay quiet. Reading a message from somebody is not sending one."
        ),
    ),
    GuardCase(
        id="messages-the-team",
        step_name="Send the team a summary on Slack",
        kind="notify",
        expect=frozenset(),
        why="Sending, but internally. An internal note is not a client email.",
    ),
    GuardCase(
        id="reads-a-signed-agreement",
        step_name="Check the signed tenancy agreement is on file",
        kind="read",
        expect=frozenset(),
        why=(
            "The counterweight to the legal cases above. Every word that makes "
            "signing dangerous is in this label, and filing a copy is clerical."
        ),
    ),
    GuardCase(
        id="notes-a-credit-check",
        step_name="Note the credit check result on the application",
        kind="write",
        expect=frozenset(),
        why=(
            "Both words of the credit note phrase, in the wrong order, meaning "
            "something else entirely. Order is what keeps that phrase usable."
        ),
    ),
    GuardCase(
        id="logs-a-credit-note",
        step_name="Log the credit note reference against the account",
        kind="write",
        expect=frozenset(),
        why="The phrase is right there, and recording one is not issuing one.",
    ),
    GuardCase(
        id="releases-a-listing",
        step_name="Release the property listing to the portals",
        kind="write",
        expect=frozenset(),
        why=(
            "Why 'release' could never be a word on its own. Listings, keys and "
            "reports all get released and none of it is money."
        ),
    ),
    GuardCase(
        id="checks-a-balance",
        step_name="Check the outstanding balance on the account",
        kind="read",
        expect=frozenset(),
        why="Why 'balance' could never be a word on its own either.",
    ),
    # The counterweights to the judgement rules above.
    GuardCase(
        id="reads-a-tenants-reply",
        step_name="Read the tenant's reply to the renewal email",
        kind="read",
        expect=frozenset(),
        why="Reading the reply is not deciding what it means. Only the decision is judgement.",
    ),
    GuardCase(
        id="branches-on-a-figure",
        step_name="Check whether the invoice is over five thousand pounds",
        kind="decision",
        expect=frozenset(),
        why="A decision on a number is a rule. Only decisions on what somebody said need a person.",
    ),
    GuardCase(
        id="logs-reported-damage",
        step_name="Log the damage the tenant reported",
        kind="write",
        expect=frozenset(),
        why="Recording a report of damage judges nothing.",
    ),
    GuardCase(
        id="records-agreed-deductions",
        step_name="Record the agreed deductions in the ledger",
        kind="write",
        expect=frozenset(),
        why="Writing down deductions somebody already agreed to is bookkeeping.",
    ),
    GuardCase(
        id="reviews-without-judging",
        step_name="Review the inventory before the check-in",
        kind="read",
        expect=frozenset(),
        why="An assessing verb alone is not enough. It has to be judging the condition of something.",
    ),
]

GUARD_CASES: list[GuardCase] = SHOULD_FIRE + SHOULD_STAY_QUIET


@dataclass(frozen=True)
class PipelineCase:
    """A description somebody might type, and what has to be true of the answer."""

    id: str
    description: str
    checks: tuple[Check, ...]
    # The recorded run to replay, where one exists. Cases without a recording
    # only run against a live model.
    replay: str | None = None
    notes: str = ""


# The descriptions are pinned here rather than imported from the API, because a
# recording is tied to the exact words that produced it. If the wording in the
# app changes, this case should keep grading what was actually recorded until
# somebody records it again on purpose.
PIPELINE_CASES: list[PipelineCase] = [
    PipelineCase(
        id="invoice-with-approval",
        replay="invoice-with-approval",
        description=(
            "Every morning I go through my emails looking for invoices. When I find "
            "one I download the PDF attachment, read the total off it, and type that "
            "into our Google Sheet. Then I message accounting on Slack to say it's "
            "in. If it's a big one, over five thousand pounds, I check with my "
            "manager first before I put it through."
        ),
        notes="Has a human check written into it. The easy case, and it should stay easy.",
        checks=(
            steps_between(3, 12),
            has_a_decision(),
            every_step_assessed(),
            something_is_automatable(),
            never_unattended("approv", "manager", "check with"),
        ),
    ),
    PipelineCase(
        id="payment-no-approval",
        replay="payment-no-approval",
        description=(
            "Every Friday I go through the supplier invoices sitting in our shared "
            "inbox. I read the amount off each one, pay it straight from our business "
            "account through the banking portal, mark it as paid in the spreadsheet, "
            "and then delete the email to keep the inbox tidy."
        ),
        notes=(
            "The important one. Nobody in this description checks anything, so any "
            "caution in the answer had to come from us rather than from the person."
        ),
        checks=(
            steps_between(3, 12),
            every_step_assessed(),
            something_is_automatable(),
            never_unattended("pay"),
            never_unattended("delete"),
            flagged(RiskFlag.MOVES_MONEY, "pay"),
            flagged(RiskFlag.IRREVERSIBLE, "delete"),
        ),
    ),
    PipelineCase(
        id="tenancy-renewals",
        description=(
            "Six weeks before a tenancy ends I check the spreadsheet for which ones "
            "are coming up. For each one I email the tenant asking if they want to "
            "renew, and I copy the landlord in. If the tenant says yes I draw up the "
            "renewal from our template and send it over for signing. If they say no "
            "I put the property back on the market and book a check out inspection."
        ),
        notes="No recording. Runs only against a live model.",
        checks=(
            steps_between(4, 14),
            has_a_decision(),
            every_step_assessed(),
            something_is_automatable(),
            # Was ("tenant", "landlord", "email"), which failed on any step that
            # mentioned a tenant: reading their reply, waiting for it, and
            # remarketing "for new tenants" after a person had confirmed they were
            # leaving. None of those is a risk, and a check that cries wolf gets
            # ignored. Narrowed on the step by step detail of four live runs, not
            # to pass it: the new list still catches both steps that were
            # genuinely wrong (deciding on the tenant's answer, twice), every
            # email to the tenant or landlord, and now also drawing up the
            # renewal agreement, which the old list missed.
            never_unattended("email", "said", "decision", "decide", "agree"),
        ),
    ),
    PipelineCase(
        id="deposit-return",
        description=(
            "When a tenancy ends I compare the check in and check out reports and "
            "decide what to take off the deposit for damage. I write up the "
            "deductions, send them to the tenant to agree, and once they agree I "
            "release the rest of the deposit back to them from the scheme. If they "
            "disagree it goes to the scheme's adjudicator and I stop touching it."
        ),
        notes=(
            "Deliberately nasty. Money, a judgement call with no rule behind it, and "
            "a regulated process. Nothing here should come back fully automatic."
        ),
        checks=(
            steps_between(4, 14),
            has_a_decision(),
            every_step_assessed(),
            asks_a_question(),
            never_unattended("deposit", "deduct", "release", "damage"),
        ),
    ),
    PipelineCase(
        id="viewing-feedback",
        description=(
            "After every viewing I ring the applicant for feedback and type what "
            "they said into the property record. Then I send the vendor a summary "
            "at the end of the week with all the feedback from that week's viewings."
        ),
        notes=(
            "The opposite risk. This is mostly harmless admin, and a tool that "
            "refuses to automate any of it is being useless rather than careful."
        ),
        checks=(
            steps_between(2, 10),
            every_step_assessed(),
            something_is_automatable(),
            never_unattended("vendor"),
        ),
    ),
]


def replayable_cases() -> list[PipelineCase]:
    return [c for c in PIPELINE_CASES if c.replay]
