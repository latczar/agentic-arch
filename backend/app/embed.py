"""Turning text into vectors, and the one place that knows how.

Separate from llm/gemini.py on purpose. That file is about generation, which is
slow, retried, and the thing that falls over on a busy afternoon. This is a
different endpoint with a different failure profile: it answers in about a
second even while generation is congested, which is the only reason retrieval is
usable on days when the rest of the pipeline is not.

Written first with urllib, since this is one POST returning a list of floats and
a dependency felt like overkill. It hung, with the request never coming back and
the timeout never firing, while the identical call through curl answered in
under a second. Rather than spend an evening on somebody's socket layer, it uses
the client the project already depends on and already knows works from here.
"""

from __future__ import annotations

import os

from app.llm.base import LLMError

MODEL = "gemini-embedding-001"

# The model returns 3072 dimensions and is trained to be truncated to fewer
# rather than merely tolerating it. 768 is ample for separating a dozen articles
# and makes the committed vector file four times smaller.
DIMENSIONS = 768

# How long one embedding call may take before the word matcher takes over.
# Generous against a healthy endpoint, which answers in about a second, and
# short enough that nobody waits on a sick one.
TIMEOUT_MS = 8000


# Held for the life of the process, which is not only an optimisation.
#
# Building one per call and using it in the same expression, as in
# `_client().models.embed_content(...)`, leaves nothing holding a reference once
# `.models` has been read. The client is then free to be collected, and its
# closing shuts the connection pool underneath the request already in flight.
# The error reads "Cannot send a request, as the client has been closed", which
# is accurate and gives no hint that the cause is the shape of the expression.
_CACHED = None


def _client():
    global _CACHED
    if _CACHED is not None:
        return _CACHED

    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise LLMError("No Gemini API key found, so text cannot be embedded.")

    try:
        from google import genai
    except ImportError as exc:  # pragma: no cover - install-time problem
        raise LLMError("The google-genai package is not installed.") from exc

    # Bounded, because retrieval is an addition to the page and the page must
    # not wait on it indefinitely. Without this a hung connection is not an
    # exception, so nothing catches it, the fallback never runs and the request
    # simply never answers. Found on a machine where Python was hanging on IPv6
    # while curl fell back to IPv4 in under a second.
    from google.genai import types

    _CACHED = genai.Client(
        api_key=key,
        http_options=types.HttpOptions(timeout=TIMEOUT_MS),
    )
    return _CACHED


def embed(text: str, *, dimensions: int = DIMENSIONS) -> list[float]:
    """One vector for one piece of text.

    Raises rather than returning nothing, because the two callers want opposite
    things: the build script should stop and say so, and the request path
    catches it and carries on without the article panel.
    """

    from google.genai import types

    try:
        response = _client().models.embed_content(
            model=MODEL,
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=dimensions),
        )
    except LLMError:
        raise
    except Exception as exc:  # the SDK raises a wide range of types
        raise LLMError(f"Embedding failed: {exc}") from exc

    embeddings = getattr(response, "embeddings", None)
    values = embeddings[0].values if embeddings else None
    if not values:
        raise LLMError("The embedding response carried no vector.")

    return [float(v) for v in values]
