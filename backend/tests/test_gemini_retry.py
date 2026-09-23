"""Being patient with a busy provider, but never past the request's deadline.

Backing off and trying again is right. Doing it without a ceiling is how a page
sits on "Working through it..." for a minute and then dies on the platform's own
timeout, which tells the reader nothing.

An earlier version of these tests checked that the pauses between retries fitted
inside the platform limit, and they passed while the live site returned 504 on
every request. The pauses were never the problem. The time spent inside each
call was never counted, and a call had no timeout. The tests below check the
property that actually matters: nothing outlives the request's deadline.

None of these reach the network. The request method is replaced outright.
"""

import time

import pytest

from app.llm.base import LLMError
from app.llm.gemini import MIN_CALL_SECONDS, GeminiClient


class Busy(Exception):
    """What a provider having a bad moment looks like from out here."""

    def __init__(self) -> None:
        super().__init__("503 This model is currently experiencing high demand.")


def client(**kwargs) -> GeminiClient:
    return GeminiClient(api_key="not-a-real-key", **kwargs)


def ask(c: GeminiClient) -> str:
    return c.generate_json(system="s", prompt="p", schema={})


# Enough for one call and no retry: the first attempt starts, and the pause
# after it would leave too little time for another.
ONE_CALL = MIN_CALL_SECONDS + 1.0


def test_it_gives_up_inside_the_budget_with_something_actionable():
    c = client(budget=ONE_CALL)
    c._request = lambda **_: (_ for _ in ()).throw(Busy())

    with pytest.raises(LLMError) as caught:
        ask(c)

    message = str(caught.value)
    assert "busy" in message
    # Somebody reading this has to know what to do next, and there is always
    # something: wait, or run a recorded case that needs no model.
    assert "try again" in message
    assert "recorded examples" in message


def test_giving_up_does_not_first_sit_through_the_sleep_it_cannot_afford():
    """Waiting and then reporting that we waited too long is the worst of both."""

    c = client(budget=ONE_CALL)
    c._request = lambda **_: (_ for _ in ()).throw(Busy())

    started = time.monotonic()
    with pytest.raises(LLMError):
        ask(c)

    assert time.monotonic() - started < 0.5


def test_a_budget_that_allows_it_still_retries_and_succeeds(monkeypatch):
    """The budget must not have turned the retry loop off altogether."""

    calls = {"n": 0}

    def flaky(**_):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Busy()
        return '{"ok": true}'

    # The backoff is real seconds. Waiting them out here buys nothing and makes
    # everybody who runs the suite pay for it.
    monkeypatch.setattr("app.llm.gemini.time.sleep", lambda _: None)

    c = client(budget=30.0)
    c._request = flaky

    assert ask(c) == '{"ok": true}'
    assert calls["n"] == 2


def test_a_permanent_failure_is_not_retried_at_all():
    """A bad key fails the same way forever, and each retry costs a person time."""

    calls = {"n": 0}

    def refused(**_):
        calls["n"] += 1
        raise Exception("400 API key not valid")

    c = client(budget=30.0)
    c._request = refused

    with pytest.raises(LLMError):
        ask(c)

    assert calls["n"] == 1


def test_each_call_is_given_only_the_time_that_is_left():
    """The bug behind the 504: a call with no timeout can outlive the request."""

    given = []

    def record(**kwargs):
        given.append(kwargs["timeout"])
        return "{}"

    c = client(budget=40.0)
    c._request = record
    ask(c)

    assert 0 < given[0] <= 40.0


def test_one_deadline_covers_every_call_in_the_request():
    """Not a fresh allowance per call, which is how six calls add up to a 504.

    Moving the deadline earlier stands in for time passing, so nothing here has
    to wait or tamper with the clock the test runner itself relies on.
    """

    given = []
    c = client(budget=40.0)

    def takes_fifteen_seconds(**kwargs):
        given.append(kwargs["timeout"])
        c._deadline -= 15.0
        return "{}"

    c._request = takes_fifteen_seconds
    ask(c)
    ask(c)

    assert given[0] == pytest.approx(40.0, abs=0.5)
    assert given[1] == pytest.approx(25.0, abs=0.5)


def test_no_call_starts_without_time_to_finish():
    """A call that would be cut off still spends quota, so it is not made."""

    calls = []
    c = client(budget=40.0)
    c._deadline = time.monotonic() + MIN_CALL_SECONDS - 1.0
    c._request = lambda **kwargs: calls.append(kwargs) or "{}"

    with pytest.raises(LLMError) as caught:
        ask(c)

    assert calls == []
    assert "did not finish" in str(caught.value)
    assert "recorded examples" in str(caught.value)


def test_a_call_that_times_out_says_so_plainly():
    """Found through the chain of causes, because the SDK wraps its timeouts."""

    def hangs(**_):
        try:
            raise TimeoutError("read timed out")
        except TimeoutError as inner:
            raise RuntimeError("request failed") from inner

    c = client(budget=40.0)
    c._request = hangs

    with pytest.raises(LLMError) as caught:
        ask(c)

    message = str(caught.value)
    assert "did not finish" in message
    assert "recorded examples" in message


def test_a_used_up_daily_allowance_says_so_and_is_not_retried():
    """The provider's own words are a JSON dump, and nobody visiting can act on it."""

    calls = {"n": 0}

    def exhausted(**_):
        calls["n"] += 1
        raise Exception(
            "Error code: 429 - Rate limit exceeded for model gemini-3.5-flash "
            "(limit: 20 requests per day on Free Tier)."
        )

    c = client(budget=40.0)
    c._request = exhausted

    with pytest.raises(LLMError) as caught:
        ask(c)

    message = str(caught.value)
    assert calls["n"] == 1
    assert "used up" in message
    assert "tomorrow" in message
    assert "recorded examples" in message
    assert "Error code" not in message


def test_the_budget_leaves_room_inside_the_platform_limit():
    """A budget that can outlast the platform is not a budget."""

    from app.llm.gemini import PLATFORM_LIMIT_SECONDS, REQUEST_BUDGET_SECONDS

    assert REQUEST_BUDGET_SECONDS < PLATFORM_LIMIT_SECONDS
    # The work around the calls (validation, the daily tally, the response)
    # needs a little time too, after the last call has returned.
    assert PLATFORM_LIMIT_SECONDS - REQUEST_BUDGET_SECONDS >= 10
    assert client().budget == REQUEST_BUDGET_SECONDS


def test_the_platform_limit_here_matches_the_one_vercel_is_told():
    """Two files have to agree, and nothing else would notice them drifting."""

    import json
    from pathlib import Path

    from app.llm.gemini import PLATFORM_LIMIT_SECONDS

    config = json.loads(
        (Path(__file__).resolve().parents[2] / "vercel.json").read_text(encoding="utf-8")
    )
    declared = config["functions"]["index.py"]["maxDuration"]

    assert declared == PLATFORM_LIMIT_SECONDS
