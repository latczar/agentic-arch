"""Is this a description of work, or a request to build something?

People type what they want, not what they do. "Can you build me a payroll
checker" is a perfectly reasonable thing to type into a box and completely the
wrong input for this tool, which needs "every Friday I go through the payroll
and check X against Y".

Sending it anyway produces a confident, useless answer and spends two model
calls doing it. Saying so first costs nothing and tells the person what would
work, which is more use than a bad process map.

Same approach as the risk detection: crude, explainable word matching, and the
error of being too quiet is preferred to the error of being too loud. A nudge
that fires on a real description is worse than one that misses a request,
because the first one blocks somebody who was doing it right.
"""

from __future__ import annotations

import re

# Ways of asking somebody to make a thing. Matched at the start, because "can
# you" in the middle of a sentence is usually somebody quoting a colleague.
REQUEST_OPENERS = (
    "can you",
    "could you",
    "can we",
    "could we",
    "would you",
    "will you",
    "please make",
    "please build",
    "please create",
    "make me",
    "build me",
    "create me",
    "give me",
    "i want a",
    "i need a",
    "i want you",
    "i need you",
    "how do i build",
    "how do i make",
    "help me build",
    "help me make",
)

# Signs somebody is describing what they actually do. Any one of these and we
# leave it alone, whatever else the text contains.
DESCRIPTION_MARKERS = (
    r"\bi (go|check|open|read|download|type|copy|send|email|log|enter|update|run|pull|look|take|write|fill|print|chase|ring|call|review)\b",
    r"\bwe (go|check|open|read|download|type|copy|send|email|log|enter|update|run|pull|look|take|write|fill|print|chase|ring|call|review)\b",
    r"\b(every|each) (day|morning|week|friday|monday|month|time|afternoon|evening)\b",
    r"\bonce a (day|week|month)\b",
    r"\bthen i\b",
    r"\bthen we\b",
    r"\bby hand\b",
    r"\bmanually\b",
)

NUDGE = (
    "That reads like a request to build something rather than a description of "
    "work you already do. This works from the second kind: what you do, in the "
    "order you do it. Something like \"every Friday I open the shared inbox, "
    "check each invoice against the payroll sheet, and flag anything that does "
    "not match\". The two examples above show the shape."
)


def looks_like_a_request(text: str) -> bool:
    """True when this is somebody asking for a tool rather than describing a job."""

    cleaned = text.strip().lower()
    if not cleaned:
        return False

    # Describing the work wins outright. Plenty of real descriptions open with a
    # polite question, and those are not what this is for.
    if any(re.search(marker, cleaned) for marker in DESCRIPTION_MARKERS):
        return False

    return cleaned.startswith(REQUEST_OPENERS)
