"""A daily budget for the public demo.

The demo needs a real model key to be worth visiting, and a public text box
wired to a metered key is a quota somebody else gets to spend. Two caps, because
they stop different things:

  - A total for the day, which protects the key. Whatever happens, the free tier
    is not exhausted by lunchtime.
  - A smaller one per visitor, which stops one person using the whole allowance
    before anybody else arrives.

Deliberately approximate. Two requests arriving together can both read the same
count and both be allowed, so the total can overshoot by a little under load.
Making it exact needs locking or a real database, and the thing being protected
is a free tier, not a bank balance. Overshooting by two is fine. Pretending it
is exact would not be.

Visitors are identified by a salted hash of their address, never the address
itself. It only has to tell two people apart for a day, and it does not need to
be reversible to do that.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)


def _missing_blob_errors() -> tuple[type[BaseException], ...]:
    """The exceptions that mean "no such object" rather than "something broke".

    The client raises for a missing object rather than returning nothing, which
    matters here more than it looks: the first request of any day reads a tally
    that does not exist yet. Treating that as a failure made the budget fail
    closed every morning, correctly and for entirely the wrong reason.

    Resolved at import and tolerant of the package being absent, so the tests
    and local development do not need it installed.
    """

    try:
        from vercel.blob import BlobNotFoundError

        return (BlobNotFoundError,)
    except ImportError:  # pragma: no cover - only when deployed
        return ()


MISSING = _missing_blob_errors()

# Flash-Lite's free tier allows several hundred requests a day and one analysis
# spends two. This leaves room for the key to be used elsewhere.
DAILY_TOTAL = 120
DAILY_PER_VISITOR = 8


@dataclass(frozen=True)
class Allowance:
    allowed: bool
    # Addressed to the visitor, so it says what they can still do rather than
    # just refusing. Empty when allowed.
    reason: str = ""


def visitor_id(address: str | None, salt: str) -> str:
    """A stable, non-reversible label for one visitor for one day.

    The date is in the hash, so yesterday's label cannot be used to recognise
    anybody today. Nothing here is stored that could identify a person later.
    """

    raw = f"{salt}:{date.today().isoformat()}:{address or 'unknown'}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def address_of(request) -> str | None:
    """The caller's address, through whatever proxy is in front of us.

    Behind a CDN the socket belongs to the CDN, so the original address is only
    in the forwarded header. The first entry is the client; the rest are hops.
    """

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return getattr(request.client, "host", None)


class MemoryBudget:
    """Counts in the process. Right for local development, useless in serverless.

    Every request can land on a different instance, so a count kept here would
    reset constantly. That is fine on one machine and no use at all deployed,
    which is why there is a second implementation.
    """

    def __init__(self, total: int = DAILY_TOTAL, per_visitor: int = DAILY_PER_VISITOR):
        self.total = total
        self.per_visitor = per_visitor
        self._day: str = ""
        self._counts: dict[str, int] = {}
        self._used = 0

    def _roll(self) -> None:
        today = date.today().isoformat()
        if self._day != today:
            self._day, self._counts, self._used = today, {}, 0

    def spend(self, visitor: str) -> Allowance:
        self._roll()
        allowance = _judge(self._used, self._counts.get(visitor, 0), self.total, self.per_visitor)
        if allowance.allowed:
            self._used += 1
            self._counts[visitor] = self._counts.get(visitor, 0) + 1
        return allowance


class BlobBudget:
    """Counts in blob storage, so every serverless instance sees the same tally.

    One object per day, which also means yesterday's counts age out on their own
    rather than needing anything scheduled.
    """

    def __init__(
        self,
        client=None,
        total: int = DAILY_TOTAL,
        per_visitor: int = DAILY_PER_VISITOR,
        prefix: str = "usage",
    ):
        self.total = total
        self.per_visitor = per_visitor
        self.prefix = prefix

        if client is not None:
            self._client = client
            return

        from vercel.blob import BlobClient

        self._client = BlobClient()

    def _path(self) -> str:
        return f"{self.prefix}/{date.today().isoformat()}.json"

    def _load(self) -> dict:
        try:
            result = self._client.get(self._path(), access="private")
        except MISSING:
            # No tally yet, which is simply the first request of the day. Not a
            # failure, and emphatically not a reason to stop serving.
            return {"used": 0, "visitors": {}}
        except Exception:
            # Anything else is a real fault. Covered in spend(): a budget that
            # cannot be read must not become a budget that is not enforced.
            log.warning("usage tally unreadable", exc_info=True)
            raise

        if result is None:
            return {"used": 0, "visitors": {}}

        content = getattr(result, "content", None)
        if isinstance(content, (bytes, bytearray)):
            return json.loads(bytes(content).decode("utf-8"))

        stream = getattr(result, "stream", None)
        if stream is not None:
            return json.loads(b"".join(stream).decode("utf-8"))

        raise ValueError("usage tally had no readable body")

    def spend(self, visitor: str) -> Allowance:
        try:
            tally = self._load()
        except Exception:
            # Fail closed. An unreadable tally means we do not know what has
            # been spent, and the entire point of this file is not finding out
            # the hard way.
            return Allowance(
                False,
                "The demo cannot check its usage limit at the moment, so it is "
                "not making model calls. The recorded examples still work.",
            )

        used = int(tally.get("used", 0))
        visitors = tally.get("visitors") or {}
        allowance = _judge(used, int(visitors.get(visitor, 0)), self.total, self.per_visitor)
        if not allowance.allowed:
            return allowance

        visitors[visitor] = int(visitors.get(visitor, 0)) + 1
        body = json.dumps(
            {"used": used + 1, "visitors": visitors, "updated": datetime.now(timezone.utc).isoformat()},
            separators=(",", ":"),
        )
        try:
            self._client.put(
                self._path(),
                body.encode("utf-8"),
                access="private",
                content_type="application/json",
                add_random_suffix=False,
                overwrite=True,
            )
        except Exception:
            # The call is allowed and the count did not save. Better to let one
            # request through uncounted than to refuse somebody over a failed
            # write, given what is at stake either way.
            log.warning("usage tally could not be written", exc_info=True)

        return allowance


def _judge(used: int, mine: int, total: int, per_visitor: int) -> Allowance:
    if mine >= per_visitor:
        return Allowance(
            False,
            f"That is {per_visitor} analyses from here today, which is the limit "
            "for one visitor on a shared demo key. The recorded examples still "
            "work, and running the project yourself has no limit at all.",
        )
    if used >= total:
        return Allowance(
            False,
            "The demo has used its model allowance for today. It resets at "
            "midnight UTC. The recorded examples still work in the meantime.",
        )
    return Allowance(True)


def open_budget():
    """Blob-backed when deployed, in-process otherwise. Same choice as the store."""

    if os.environ.get("BLOB_READ_WRITE_TOKEN") or os.environ.get("BLOB_STORE_ID"):
        return BlobBudget()
    return MemoryBudget()


def budget_salt() -> str:
    """Salt for visitor hashing.

    A per-deployment value is enough: it only has to stop the hashes being a
    plain lookup table of addresses.
    """

    return os.environ.get("VERCEL_DEPLOYMENT_ID") or os.environ.get("HOSTNAME") or "local"
