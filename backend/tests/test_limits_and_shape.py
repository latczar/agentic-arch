"""The two guards in front of a shared model key.

One stops a public text box spending somebody else's quota. The other stops it
spending anything at all on an input that was never going to work.
"""

import json

import pytest

from app.limits import (
    Allowance,
    BlobBudget,
    MemoryBudget,
    address_of,
    visitor_id,
)
from app.shape import looks_like_a_request


class FakeRequest:
    def __init__(self, headers: dict | None = None, host: str | None = None):
        self.headers = headers or {}
        self.client = type("C", (), {"host": host})()


# The real exception, not a stand-in. The SDK is a declared dependency, and the
# last bug here came from a fake that had drifted from the thing it stood in
# for, so where the genuine article is available it gets used.
from vercel.blob import BlobNotFoundError as NotFound  # noqa: E402


class FakeBlob:
    """Raises for a missing object, which is what the real client does.

    Worth stating plainly, because assuming it returned nothing is what broke
    the budget on its first real request: the tally for a new day does not exist
    yet, the read raised, and the whole thing failed closed every morning.
    """

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.fail_reads = False
        self.fail_writes = False

    def get(self, pathname, *, access, **kw):
        if self.fail_reads:
            raise RuntimeError("blob store unavailable")
        if pathname not in self.objects:
            raise NotFound()
        return type("R", (), {"status_code": 200, "content": self.objects[pathname]})()

    def put(self, pathname, body, **kw):
        if self.fail_writes:
            raise RuntimeError("blob store unavailable")
        self.objects[pathname] = bytes(body)
        return {"pathname": pathname}


# --- Who is asking -----------------------------------------------------------


def test_the_address_comes_from_the_forwarded_header_behind_a_proxy():
    """Behind a CDN the socket belongs to the CDN, not the visitor."""

    request = FakeRequest({"x-forwarded-for": "203.0.113.7, 70.41.3.18"}, host="10.0.0.1")
    assert address_of(request) == "203.0.113.7"


def test_the_socket_address_is_used_when_there_is_no_proxy():
    assert address_of(FakeRequest(host="127.0.0.1")) == "127.0.0.1"


def test_a_missing_address_does_not_blow_up():
    assert address_of(FakeRequest()) is None


def test_the_visitor_label_does_not_contain_the_address():
    """Nothing stored should be able to identify somebody later."""

    label = visitor_id("203.0.113.7", "salt")
    assert "203.0.113.7" not in label
    assert len(label) == 16


def test_the_same_visitor_gets_the_same_label():
    assert visitor_id("203.0.113.7", "s") == visitor_id("203.0.113.7", "s")


def test_different_visitors_get_different_labels():
    assert visitor_id("203.0.113.7", "s") != visitor_id("203.0.113.8", "s")


def test_a_different_salt_gives_a_different_label():
    assert visitor_id("203.0.113.7", "a") != visitor_id("203.0.113.7", "b")


# --- Spending the budget ------------------------------------------------------


@pytest.mark.parametrize("budget", [MemoryBudget(total=100, per_visitor=3),
                                    BlobBudget(client=FakeBlob(), total=100, per_visitor=3)])
def test_one_visitor_cannot_use_more_than_their_share(budget):
    for _ in range(3):
        assert budget.spend("alice").allowed

    refused = budget.spend("alice")
    assert not refused.allowed
    assert "3 analyses" in refused.reason


@pytest.mark.parametrize("budget", [MemoryBudget(total=100, per_visitor=2),
                                    BlobBudget(client=FakeBlob(), total=100, per_visitor=2)])
def test_one_visitor_running_out_does_not_affect_anybody_else(budget):
    budget.spend("alice")
    budget.spend("alice")
    assert not budget.spend("alice").allowed
    assert budget.spend("bob").allowed


@pytest.mark.parametrize("budget", [MemoryBudget(total=3, per_visitor=99),
                                    BlobBudget(client=FakeBlob(), total=3, per_visitor=99)])
def test_the_day_has_a_ceiling_whoever_is_asking(budget):
    """The cap that actually protects the key."""

    for n in range(3):
        assert budget.spend(f"visitor{n}").allowed

    refused = budget.spend("visitor4")
    assert not refused.allowed
    assert "allowance for today" in refused.reason


def test_the_first_request_of_the_day_is_allowed():
    """No tally exists yet, and that is not a failure.

    The bug this pins: a missing object raises rather than returning nothing, so
    every morning's first request looked like the store was broken and the
    budget refused. Correct behaviour for the wrong reason is still a fault.
    """

    budget = BlobBudget(client=FakeBlob(), total=10, per_visitor=5)

    allowance = budget.spend("alice")
    assert allowance.allowed, allowance.reason


def test_a_missing_tally_is_not_confused_with_a_broken_store():
    """The two look identical from here and must not be treated the same."""

    blob = FakeBlob()
    budget = BlobBudget(client=blob, total=10, per_visitor=5)

    assert budget.spend("alice").allowed  # missing: allowed
    blob.fail_reads = True
    assert not budget.spend("bob").allowed  # broken: refused


def test_an_unreadable_tally_fails_closed():
    """Not knowing what has been spent must not mean spending freely."""

    blob = FakeBlob()
    budget = BlobBudget(client=blob, total=100, per_visitor=10)
    blob.fail_reads = True

    refused = budget.spend("alice")
    assert not refused.allowed
    assert "recorded examples still work" in refused.reason


def test_a_failed_write_still_lets_the_call_through():
    """The opposite direction, and deliberate.

    A request that was allowed should not be refused because a counter could not
    be saved. One uncounted call is a smaller problem than a demo that turns
    itself off when storage hiccups.
    """

    blob = FakeBlob()
    budget = BlobBudget(client=blob, total=100, per_visitor=10)
    blob.fail_writes = True

    assert budget.spend("alice").allowed


def test_the_tally_is_written_where_it_can_be_read_back():
    blob = FakeBlob()
    budget = BlobBudget(client=blob, total=100, per_visitor=10)
    budget.spend("alice")
    budget.spend("alice")

    (stored,) = blob.objects.values()
    tally = json.loads(stored.decode("utf-8"))

    assert tally["used"] == 2
    assert sum(tally["visitors"].values()) == 2


def test_the_tally_is_kept_per_day_so_it_ages_out_on_its_own():
    blob = FakeBlob()
    BlobBudget(client=blob, total=10, per_visitor=10).spend("alice")

    (path,) = blob.objects.keys()
    assert path.startswith("usage/")
    assert path.endswith(".json")


def test_an_allowance_that_passed_carries_no_complaint():
    assert Allowance(True).reason == ""


# --- Is this even the right kind of input -------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "can you create a billing/payroll checker for me?",
        "Could you build me something that chases late invoices",
        "make me a tool for tracking viewings",
        "I need a system that reconciles the rent account",
        "please build an app to handle tenancy renewals",
        "how do i build an automation for this",
    ],
)
def test_a_request_to_build_something_is_recognised(text):
    assert looks_like_a_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "Every Friday I go through the supplier invoices in the shared inbox.",
        "I check the spreadsheet for tenancies ending, then email each tenant.",
        "Once a week we pull the report and copy the totals across by hand.",
        "Each morning I open the inbox and download any invoice attachments.",
        "We do this manually at the moment and it takes about an hour.",
    ],
)
def test_a_description_of_real_work_is_left_alone(text):
    assert not looks_like_a_request(text)


def test_a_polite_description_is_not_mistaken_for_a_request():
    """The false positive that would matter: blocking somebody doing it right."""

    text = (
        "Can you tell me about this one: every Monday I check the portal, "
        "download the leads, and type them into the CRM."
    )
    assert not looks_like_a_request(text)


def test_a_request_mentioning_a_process_still_gets_the_nudge():
    assert looks_like_a_request("Can you make me a payroll checker please")


def test_empty_input_is_not_a_request():
    assert not looks_like_a_request("   ")
