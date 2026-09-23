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
    "help me check",
    "help me with",
)

# Bare imperatives, which is how people actually talk to a box with an AI
# behind it. "create a pdf payslip for my outgoing accounts then email it to me"
# matched nothing above, because everything above is a polite form, and it cost
# a model call and a minute of somebody's afternoon to find that out.
#
# Each one needs a determiner after it, and that is the whole safety margin.
# "Create a..." is asking for something to be made. "Check each invoice against
# the sheet" is somebody describing their Friday in the imperative, and blocking
# that is the loud failure this module exists to avoid. So: verbs that build,
# and only where a thing is being named straight after them.
BUILD_VERBS = ("create", "build", "make", "generate", "design", "develop", "set up")
DETERMINERS = ("a", "an", "the", "me a", "me an")

BUILD_REQUESTS = tuple(
    f"{verb} {determiner} " for verb in BUILD_VERBS for determiner in DETERMINERS
) + (
    # No determiner needed. Nobody describes work they already do by hand by
    # opening with the word automate.
    "automate ",
)

# Nobody types a clean sentence into a box. "so can u help me check" walked
# straight past a matcher anchored to the start, because of a leading "so" and
# a "u", and produced a confident analysis of nothing. Stripped before matching
# rather than added to the list above, which would need every combination.
LEAD_IN = ("so ", "ok ", "okay ", "hi ", "hey ", "hello ", "um ", "uh ", "well ", "right ")

# Chat shorthand, normalised to the words the openers are written in.
SHORTHAND = {
    "u": "you",
    "ur": "your",
    "pls": "please",
    "plz": "please",
    "wud": "would",
    "cud": "could",
    "n": "and",
}

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
    # Doing one thing per item is what a repeated job looks like, and it rescues
    # the imperative descriptions the build verbs below would otherwise catch:
    # "create a row in the sheet for every application" is a step, not an order.
    # It will occasionally let a real request through ("build a dashboard for
    # each branch"), which is the direction this module prefers to be wrong in.
    r"\bfor (every|each) \w+",
    r"\bwhenever\b",
)

NUDGE = (
    "That reads like a request to build something rather than a description of "
    "work you already do. This works from the second kind: what you do, in the "
    "order you do it. Something like \"every Friday I open the shared inbox, "
    "check each invoice against the payroll sheet, and flag anything that does "
    "not match\". The two examples above show the shape."
)


def normalise(text: str) -> str:
    """Lower case, chat shorthand expanded, and any throat-clearing removed."""

    words = [SHORTHAND.get(w, w) for w in text.strip().lower().split()]
    cleaned = " ".join(words)

    # Repeatedly, because "so ok can you" is a thing people type.
    changed = True
    while changed:
        changed = False
        for opener in LEAD_IN:
            if cleaned.startswith(opener):
                cleaned = cleaned[len(opener) :]
                changed = True

    return cleaned


def looks_like_a_request(text: str) -> bool:
    """True when this is somebody asking for a tool rather than describing a job."""

    cleaned = normalise(text)
    if not cleaned:
        return False

    # Describing the work wins outright. Plenty of real descriptions open with a
    # polite question, and those are not what this is for.
    if any(re.search(marker, cleaned) for marker in DESCRIPTION_MARKERS):
        return False

    return cleaned.startswith(REQUEST_OPENERS) or cleaned.startswith(BUILD_REQUESTS)
