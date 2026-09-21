"""A deliberately thin seam between our code and whichever model provider we use.

The whole interface is one method that takes a prompt and a schema and returns
raw JSON text. That is all we need, and keeping it that small is the point: the
provider stops being a decision we are married to. Swapping Gemini for another
provider, or adding a second to compare them, is one new file implementing this.

Providers change their SDKs often. The Gemini one changed shape between our
training assumptions and today. A seam this narrow means that churn touches one
file instead of the whole codebase.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class LLMError(RuntimeError):
    """Anything that went wrong talking to a model provider."""


@runtime_checkable
class StructuredLLM(Protocol):
    """A model that can be asked to return JSON matching a schema."""

    name: str

    def generate_json(self, *, system: str, prompt: str, schema: dict) -> str:
        """Return raw JSON text conforming to `schema`.

        Returns text rather than a parsed object on purpose. Parsing and
        validation are our job, not the provider's. We want the raw response
        available when something goes wrong, so it can be logged and shown back
        to the model in a repair attempt.
        """
        ...
