"""Labelled queries for the playbook retrievers.

Same shape as the guard cases: both directions, scored separately. A retriever
that always returns its closest guess scores brilliantly on the first list and
is worthless, because the thing it will actually do in front of somebody is
attach a confident, irrelevant article to a process it has never seen.

The queries are written the way people type, not the way documents are written.
Cleaning them up first would be measuring a problem nobody has.

Everything here is invented. The processes are shaped like estate agency work
because that is the domain, but no real agency, property or person appears.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlaybookCase:
    query: str
    expects: str | None
    why: str


# --- Should find the right article -------------------------------------------

SHOULD_MATCH: tuple[PlaybookCase, ...] = (
    PlaybookCase(
        "we sort out the rent money each month",
        "rent-collection",
        "Almost no shared vocabulary with the title. The hard case for word matching.",
    ),
    PlaybookCase(
        "chasing tenants who have not paid yet",
        "rent-collection",
        "Hits the aliases rather than the title.",
    ),
    PlaybookCase(
        "every friday I go through the supplier invoices in the inbox and pay them "
        "from the business account",
        "supplier-invoices",
        "The easy case. If this misses, something is badly wrong.",
    ),
    PlaybookCase(
        "someone enquires about a flat and I have to get back to them and book them in to see it",
        "applicant-to-viewing",
        "Describes the job without using the words the article is titled with.",
    ),
    PlaybookCase(
        "tenant rings up saying the boiler is broken and I have to get someone out to fix it",
        "maintenance-jobs",
        "A concrete instance of an abstract article.",
    ),
    PlaybookCase(
        "the fixed term is ending soon and I need to work out if they are staying "
        "and what the rent should be",
        "tenancy-renewal",
        "Competes with rent-collection, which also talks about rent constantly.",
    ),
    PlaybookCase(
        "working out what to pay each landlord after taking our fee and any works off",
        "landlord-statements",
        "Competes with supplier-invoices, since both are about paying people.",
    ),
    PlaybookCase(
        "checking which gas certificates are about to run out and booking the engineer",
        "compliance-certificates",
        "Competes with maintenance-jobs, since both send a tradesperson to a property.",
    ),
    PlaybookCase(
        "create a pdf payslip for my outgoing accounts then email to me and message via whatsapp",
        "contractor-payroll",
        "Typed into the live site by a real user, who got a timeout. Kept verbatim.",
    ),
    PlaybookCase(
        "monthly wages run for the team",
        "contractor-payroll",
        "Four words, all alias. The short-query case.",
    ),
    PlaybookCase(
        "getting the photos and a description up on the portals and our own website",
        "listing-a-property",
        "Never says listing or marketing.",
    ),
    PlaybookCase(
        "collecting passports and proof of address and checking they are allowed to rent here",
        "id-and-referencing",
        "Never says identity, referencing or right to rent in those words.",
    ),
    PlaybookCase(
        "buyer puts in an offer, I take it to the seller, and if they agree I send the memo out",
        "offer-to-sale",
        "Straightforward, and a useful control against the harder ones.",
    ),
    PlaybookCase(
        "tenant is moving out so we inspect the place and work out what comes off their deposit",
        "end-of-tenancy",
        "Competes with maintenance-jobs on inspection vocabulary.",
    ),
)


# --- Should find nothing at all ----------------------------------------------
#
# Real work, genuinely absent from the corpus. Several deliberately borrow a
# word or two from an article that must not win on it. This half is the whole
# reason the retrievers have a threshold, and it is where a system that always
# returns its best guess falls over.

SHOULD_FIND_NOTHING: tuple[PlaybookCase, ...] = (
    PlaybookCase(
        "I restock the vending machine in reception every Tuesday",
        None,
        "Nothing like it in the corpus, and no borrowed vocabulary.",
    ),
    PlaybookCase(
        "each morning I water the office plants and empty the dishwasher",
        None,
        "Routine and repetitive, which is not the same as being covered.",
    ),
    PlaybookCase(
        "I reconcile the coffee fund at the end of every month",
        None,
        "Borrows reconcile and month from two accounting articles on purpose.",
    ),
    PlaybookCase(
        "I book flights and hotels for the directors when they are travelling",
        None,
        "Borrows book, which two articles use for appointments.",
    ),
    PlaybookCase(
        "sorting out the parking permits for the staff car park",
        None,
        "Borrows sorting out, which appears in a matched query above.",
    ),
    PlaybookCase(
        "writing the monthly newsletter and posting it on social media",
        None,
        "Borrows monthly and posting. Marketing is simply not covered here.",
    ),
    PlaybookCase(
        "updating the holiday calendar when somebody books time off",
        None,
        "Staff admin. Borrows books and updating.",
    ),
    PlaybookCase(
        "I check the fire alarm panel every week and write it in the log book",
        None,
        "The nastiest one. Checking, weekly, logged, and a safety flavour that "
        "pulls hard towards the certificates article without being it.",
    ),
)


ALL_CASES = SHOULD_MATCH + SHOULD_FIND_NOTHING
