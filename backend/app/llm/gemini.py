"""Gemini implementation of the StructuredLLM seam.

Chosen because its free tier is the most usable one for schema-constrained
output. Nothing else in the codebase imports google.genai. If that stops being
true, the seam has leaked.
"""

from __future__ import annotations

import os
import random
import time

from app.llm.base import LLMError

# Flash rather than Flash-Lite, which is not the way round it sounds.
#
# Lite was chosen originally on quota: the free tier allows far more requests a
# day on the smaller models, and extraction is mechanical work that does not
# need a large one. Sound reasoning, and it stopped being true. Measured against
# the same trivial prompt, three samples each:
#
#     gemini-3.5-flash-lite    42.8s   68.8s   34.4s
#     gemini-3.5-flash          6.0s   15.7s    4.1s
#
# An analysis makes two of these calls, so Lite could not finish inside Vercel's
# 60 second limit and the deployed site returned 504 on every typed request. A
# cheaper model you cannot finish a request on is not cheaper.
#
# Worth re-measuring rather than trusting: this is a snapshot of one afternoon,
# and it is one line to change back.
DEFAULT_MODEL = "gemini-3.5-flash"

# HTTP statuses worth trying again: rate limited, or the provider is briefly
# unwell. Anything else (a bad key, a malformed schema) will fail identically
# however many times we ask, so retrying it just wastes the user's time.
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})

# How long one call may spend being patient before it gives up and says so.
#
# Backing off four times doubling from two seconds is fine in isolation and adds
# up badly: a single analysis makes two of these calls, each of which may repair
# itself twice more, so a busy afternoon at the provider turns a page load into
# several minutes of nothing.
#
# The number is derived rather than picked. vercel.json allows a function 60
# seconds, an analysis makes two calls, so neither may spend more than half of
# what is left after a little room for the work itself. Set it above that and
# the platform kills the request first, which produces a 504 the reader cannot
# act on instead of a sentence telling them what to do.
#
# Found the hard way, with a browser sitting on "Working through it..." for two
# minutes and then showing exactly that 504.
PLATFORM_LIMIT_SECONDS = 60.0
CALLS_PER_ANALYSIS = 2
RETRY_BUDGET_SECONDS = 25.0


class GeminiClient:
    """Talks to Google AI Studio."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_retries: int = 4,
        retry_budget: float = RETRY_BUDGET_SECONDS,
    ) -> None:
        self.max_retries = max_retries
        self.retry_budget = retry_budget
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

        self._client = genai.Client(api_key=key)
        self.model = model
        self.name = f"gemini:{model}"

    def generate_json(self, *, system: str, prompt: str, schema: dict) -> str:
        """Ask for JSON, retrying through the provider having a bad moment.

        Distinct from the repair loop in extract.py. That one handles a reply
        that arrived and was wrong; this handles a reply that never arrived.
        The delay doubles each time and carries a little randomness, so that a
        burst of callers does not all come back at the same instant and cause
        the pile-up again. That is standard exponential backoff with jitter.
        """

        delay = 2.0
        started = time.monotonic()

        for attempt in range(1, self.max_retries + 1):
            try:
                return self._request(system=system, prompt=prompt, schema=schema)
            except LLMError:
                raise
            except Exception as exc:  # provider SDKs raise a wide range of types
                if not _is_transient(exc) or attempt == self.max_retries:
                    raise LLMError(f"Gemini request failed: {exc}") from exc

                wait = delay + random.uniform(0, 1)

                # Stop before the sleep that would take us past the budget,
                # rather than after. Waiting first and then reporting that we
                # waited too long is the worst of both.
                if time.monotonic() - started + wait > self.retry_budget:
                    raise LLMError(
                        f"{self.model} is busy and did not answer within "
                        f"{self.retry_budget:.0f} seconds. Give it a minute and "
                        "try again, or run one of the recorded examples, which "
                        "need no model at all."
                    ) from exc

                print(f"  {self.model} busy, retrying in {wait:.1f}s "
                      f"(attempt {attempt} of {self.max_retries})...")
                time.sleep(wait)
                delay *= 2

        raise LLMError("Unreachable: retry loop exited without returning.")

    def _request(self, *, system: str, prompt: str, schema: dict) -> str:
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
        )

        text = getattr(interaction, "output_text", None)
        if not text:
            raise LLMError("Gemini returned an empty response.")
        return text


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
