"""Being patient with a busy provider, but not indefinitely.

Backing off and trying again is right. Doing it without a ceiling is how a page
sits on "Working through it..." for two minutes and then dies on the platform's
own timeout, which tells the reader nothing.

None of these reach the network. The request method is replaced outright.
"""

import time

import pytest

from app.llm.base import LLMError
from app.llm.gemini import GeminiClient


class Busy(Exception):
    """What a provider having a bad moment looks like from out here."""

    def __init__(self) -> None:
        super().__init__("503 This model is currently experiencing high demand.")


def client(**kwargs) -> GeminiClient:
    return GeminiClient(api_key="not-a-real-key", **kwargs)


def ask(c: GeminiClient) -> str:
    return c.generate_json(system="s", prompt="p", schema={})


def test_it_gives_up_inside_the_budget_with_something_actionable():
    c = client(retry_budget=0.0)
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

    c = client(retry_budget=0.0)
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

    c = client(retry_budget=30.0)
    c._request = flaky

    assert ask(c) == '{"ok": true}'
    assert calls["n"] == 2


def test_a_permanent_failure_is_not_retried_at_all():
    """A bad key fails the same way forever, and each retry costs a person time."""

    calls = {"n": 0}

    def refused(**_):
        calls["n"] += 1
        raise Exception("400 API key not valid")

    c = client(retry_budget=30.0)
    c._request = refused

    with pytest.raises(LLMError):
        ask(c)

    assert calls["n"] == 1


def test_the_default_budget_fits_inside_the_platform_timeout():
    """The whole point of the budget, so it is worth asserting rather than assuming.

    A budget that can outlast the platform is not a budget. Vercel kills the
    request first and the reader gets a 504 saying nothing.
    """

    from app.llm.gemini import (
        CALLS_PER_ANALYSIS,
        PLATFORM_LIMIT_SECONDS,
        RETRY_BUDGET_SECONDS,
    )

    assert RETRY_BUDGET_SECONDS * CALLS_PER_ANALYSIS < PLATFORM_LIMIT_SECONDS
    assert client().retry_budget == RETRY_BUDGET_SECONDS


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
