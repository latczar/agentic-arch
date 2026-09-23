"""Gemini implementation of the StructuredLLM seam.

Chosen because its free tier is the most usable one for schema-constrained
output. Nothing else in the codebase imports google.genai. If that stops being
true, the seam has leaked.
"""

from __future__ import annotations

import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from app.llm.base import LLMError

# Flash-Lite, measured on the real workload rather than a toy prompt.
#
# Timed on 24 September 2026, a full analysis (map, then judge), three runs:
#
#     gemini-3.5-flash-lite    19.6s   18.3s   21.2s    every call 8 to 13s
#     gemini-3.5-flash         over 60s on the live site, twice that day
#
# An earlier benchmark sent "say ok" and found Lite taking 34 to 68 seconds, so
# the default was switched to Flash. That test had nothing in common with the
# real work: no schema, no system prompt, a two word answer. Flash then timed
# out on every typed request, and its free tier turned out to allow 20 requests
# a day, which one analysis can spend a quarter of.
#
# The lesson is in the numbers above: measure the workload you actually run.
DEFAULT_MODEL = "gemini-3.5-flash-lite"

# HTTP statuses worth trying again: rate limited, or the provider is briefly
# unwell. Anything else (a bad key, a malformed schema) will fail identically
# however many times we ask, so retrying it just wastes the user's time.
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})

# How long the platform lets one request run. Must match vercel.json, and a test
# checks that it does. Vercel allows up to 300 on the free plan; 120 is plenty
# for an analysis that normally takes 20, and nobody waits longer anyway.
PLATFORM_LIMIT_SECONDS = 120.0

# How long one request may spend on the model, in total: every call, every
# repair attempt and every pause between retries.
#
# The earlier version budgeted only the pauses, 25 seconds a call for two calls.
# Time spent inside a call was never counted, a call had no timeout at all, and
# an analysis can make six calls, not two, once repairs are included. So one slow
# reply held the request open until the platform killed it, and the reader got a
# 504 instead of a sentence.
#
# One deadline for the whole request fixes all three. The 20 seconds left over
# covers the work around the calls, with room to spare.
REQUEST_BUDGET_SECONDS = 100.0

# Not worth starting a call with less than this left. It would almost certainly
# be cut off, and a wasted call still counts against the day's quota.
MIN_CALL_SECONDS = 5.0


class GeminiClient:
    """Talks to Google AI Studio.

    One client serves one request, and its budget starts when it is made. Every
    call it makes is given only the time that is left.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_retries: int = 4,
        budget: float = REQUEST_BUDGET_SECONDS,
    ) -> None:
        self.max_retries = max_retries
        self.budget = budget

        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMError(
                "No Gemini API key found. Get a free one at https://aistudio.google.com "
                'then run:  setx GEMINI_API_KEY "your-key"  and open a new terminal.'
            )

        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - install-time problem
            raise LLMError("The google-genai package is not installed. Run: pip install google-genai") from exc

        from google.genai import types

        # The SDK retries by itself underneath our own loop: 429s and 5xx
        # errors, up to three times, sleeping for whatever the server's
        # Retry-After says. On a used-up daily allowance that header says about
        # a minute, so the reader waited three minutes to be told "try again
        # tomorrow". One retry loop, ours, which knows about the deadline.
        # The SDK still makes one extra attempt on a 429 even at zero, which is
        # measured and tolerated: the deadline below is what actually holds.
        self._client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=0)),
        )
        self.model = model
        self.name = f"gemini:{model}"
        self.last_usage: dict[str, int] = {}

        # Started once the client is ready, so the first import of the SDK,
        # which takes about a second, is not charged to the model's time.
        self._deadline = time.monotonic() + budget

    def remaining(self) -> float:
        return self._deadline - time.monotonic()

    def generate_json(self, *, system: str, prompt: str, schema: dict) -> str:
        """Ask for JSON, retrying through the provider having a bad moment.

        Distinct from the repair loop in extract.py. That one handles a reply
        that arrived and was wrong; this handles a reply that never arrived.
        The delay doubles each time and carries a little randomness, so that a
        burst of callers does not all come back at the same instant and cause
        the pile-up again. That is standard exponential backoff with jitter.
        """

        delay = 2.0

        for attempt in range(1, self.max_retries + 1):
            left = self.remaining()
            if left < MIN_CALL_SECONDS:
                raise LLMError(self._out_of_time())

            try:
                return _within(
                    left,
                    lambda: self._request(system=system, prompt=prompt, schema=schema, timeout=left),
                )
            except _Late:
                raise LLMError(self._out_of_time()) from None
            except LLMError:
                raise
            except Exception as exc:  # provider SDKs raise a wide range of types
                if _is_timeout(exc):
                    raise LLMError(self._out_of_time()) from exc
                if _is_daily_quota(exc):
                    raise LLMError(
                        "Today's free model allowance has been used up, so new "
                        "descriptions cannot be analysed until it resets tomorrow. "
                        "The recorded examples still work, and need no model at all."
                    ) from exc
                if not _is_transient(exc) or attempt == self.max_retries:
                    raise LLMError(f"Gemini request failed: {exc}") from exc

                wait = delay + random.uniform(0, 1)

                # Stop before a sleep that would leave no time for the call
                # after it, rather than sleeping and then running out.
                if self.remaining() - wait < MIN_CALL_SECONDS:
                    raise LLMError(
                        f"{self.model} is busy and did not answer in time. Give it "
                        "a minute and try again, or run one of the recorded "
                        "examples, which need no model at all."
                    ) from exc

                print(f"  {self.model} busy, retrying in {wait:.1f}s "
                      f"(attempt {attempt} of {self.max_retries})...")
                time.sleep(wait)
                delay *= 2

        raise LLMError("Unreachable: retry loop exited without returning.")

    def _out_of_time(self) -> str:
        return (
            f"{self.model} did not finish within the {self.budget:.0f} seconds this "
            "page allows. Try again in a minute, or run one of the recorded "
            "examples, which need no model at all."
        )

    def _request(self, *, system: str, prompt: str, schema: dict, timeout: float) -> str:
        # The Interactions API takes a single input string, so the system
        # instruction is prepended rather than passed separately.
        interaction = self._client.interactions.create(
            model=self.model,
            input=f"{system}\n\n---\n\n{prompt}",
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
            timeout=timeout,
        )

        # Kept for anybody measuring cost, such as scripts/bakeoff.py. Thinking
        # tokens are counted apart from the answer and billed as output, which
        # is how a model can cost more than its answer length suggests.
        usage = getattr(interaction, "usage", None)
        self.last_usage = {
            "input": getattr(usage, "total_input_tokens", None) or 0,
            "output": getattr(usage, "total_output_tokens", None) or 0,
            "thinking": getattr(usage, "total_thought_tokens", None) or 0,
            "total": getattr(usage, "total_tokens", None) or 0,
        }

        text = getattr(interaction, "output_text", None)
        if not text:
            raise LLMError("Gemini returned an empty response.")
        return text


class _Late(Exception):
    """The call was still going when the deadline arrived."""


def _within(seconds: float, call):
    """Run a call, and stop waiting for it after `seconds`, whatever it is doing.

    The timeout handed to the SDK is not a deadline. It limits how long the
    connection may sit silent, so a reply arriving a byte at a time never trips
    it: measured against a local server sending one byte a second, a 3 second
    timeout returned after 11. And a live Flash call given 100 seconds ran for
    over 200. Only waiting on the clock ourselves makes the deadline real.

    The abandoned call carries on in its thread until the SDK gives up on it.
    Nothing reads its answer, and on Vercel the instance is frozen once the
    response has gone, so the cost is one request's worth of quota.
    """

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(call).result(timeout=seconds)
    except FutureTimeout:
        raise _Late() from None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _causes(exc: BaseException | None):
    """The exception and everything it was raised from, since SDKs wrap."""

    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _is_timeout(exc: Exception) -> bool:
    """Did the call run out of time? Matched by name, so no SDK import is needed.

    The SDK raises APITimeoutError, wrapping httpx's own timeout types, and the
    standard library has TimeoutError. All of them say so in the class name.
    """

    return any("timeout" in type(e).__name__.lower() for e in _causes(exc))


def _is_daily_quota(exc: Exception) -> bool:
    """A 429 that will not clear today, however long we wait."""

    text = str(exc).lower()
    return "429" in text and any(
        phrase in text for phrase in ("per day", "daily", "quota exceeded")
    )


def _is_transient(exc: Exception) -> bool:
    """Is this worth trying again, or will it fail the same way forever?

    Not every 429 is alike, and the difference costs real quota. A per-minute
    burst limit clears in a minute, so waiting is right. A daily quota does not
    clear today, and on a metered tier each doomed retry can count against the
    allowance that has already run out, so we stop immediately and say so.
    """

    text = str(exc).lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if not isinstance(status, int):
        status = next((c for c in TRANSIENT_STATUSES if str(c) in text), None)

    if status == 429:
        return not any(phrase in text for phrase in ("per day", "daily", "quota exceeded"))

    return status in TRANSIENT_STATUSES
