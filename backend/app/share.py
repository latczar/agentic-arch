"""Storage for shared analyses, so a result can be sent to somebody else.

Three things this has to get right, whatever it is stored in, and they are all
about the fact that a shared analysis describes how somebody's business runs:

  - The identifier is unguessable. Sequential ids would let anyone walk the
    whole store and read every process anybody has ever analysed.
  - Shares expire. An unlisted link that lives forever is a slow leak, and
    nobody comes back to tidy up.
  - Size is capped. A public write endpoint with no limit is somebody else's
    free storage.

There are two implementations, because the answer changes with where this runs.
SQLite is right on a machine with a disk: boring, no configuration, no account.
Hosted, there is no disk that survives a request, so the same interface is
served by Vercel Blob instead. `open_store()` picks, and nothing above here
knows which one it got.

The seam is the same idea as the one in front of the model provider. Storage is
a deployment decision, not an application one, and the application should not
have to care.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

log = logging.getLogger(__name__)

DEFAULT_DB = Path(__file__).resolve().parents[1] / "shares.db"

# Long enough that guessing one is hopeless: 12 url-safe characters is about 71
# bits. These links are unlisted rather than secret, but unlisted should mean
# genuinely unfindable.
ID_BYTES = 9

TTL_DAYS = 30

# A generous ceiling for a real analysis and a low one for anybody thinking of
# using this as a pastebin.
MAX_PAYLOAD_BYTES = 256 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS shares (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    title       TEXT NOT NULL,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS shares_expires_at ON shares (expires_at);
"""


class ShareTooLarge(ValueError):
    """The payload is bigger than we are willing to store."""


class ShareRecord(BaseModel):
    id: str
    title: str
    created_at: datetime
    expires_at: datetime
    payload: dict

    def encoded(self) -> str:
        return self.model_dump_json()

    @classmethod
    def decoded(cls, text: str) -> ShareRecord:
        return cls.model_validate_json(text)


@runtime_checkable
class ShareStorage(Protocol):
    """Everything above this line needs exactly these three things."""

    def create(self, payload: dict, title: str, ttl_days: int = TTL_DAYS) -> ShareRecord: ...

    def get(self, share_id: str) -> ShareRecord | None: ...

    def count(self) -> int: ...


def _new_record(payload: dict, title: str, ttl_days: int) -> tuple[ShareRecord, str]:
    """Build a record and its encoded form, refusing anything oversized.

    Shared by both stores so the id length, the expiry and the size cap cannot
    drift apart between them. Those three are the security properties; having
    one implementation quietly enforce a different limit is how a rule becomes
    a suggestion.
    """

    encoded = json.dumps(payload, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ShareTooLarge(
            "That analysis is too large to share. This is sized for a process, "
            "not a document."
        )

    now = datetime.now(timezone.utc)
    record = ShareRecord(
        id=secrets.token_urlsafe(ID_BYTES),
        title=title[:200] or "Untitled process",
        created_at=now,
        expires_at=now + timedelta(days=ttl_days),
        payload=payload,
    )
    return record, encoded


class ShareStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.executescript(SCHEMA)
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def create(self, payload: dict, title: str, ttl_days: int = TTL_DAYS) -> ShareRecord:
        record, encoded = _new_record(payload, title, ttl_days)

        with closing(self._connect()) as db:
            db.execute(
                "INSERT INTO shares (id, created_at, expires_at, title, payload)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.created_at.isoformat(),
                    record.expires_at.isoformat(),
                    record.title,
                    encoded,
                ),
            )
            db.commit()

        return record

    def get(self, share_id: str) -> ShareRecord | None:
        """Return a share, or None if it does not exist or has expired.

        Expired rows are treated as absent and swept on the way past, so the
        table tidies itself without anything scheduled.
        """

        now = datetime.now(timezone.utc)

        with closing(self._connect()) as db:
            db.execute("DELETE FROM shares WHERE expires_at < ?", (now.isoformat(),))
            db.commit()

            row = db.execute("SELECT * FROM shares WHERE id = ?", (share_id,)).fetchone()

        if row is None:
            return None

        return ShareRecord(
            id=row["id"],
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            payload=json.loads(row["payload"]),
        )

    def count(self) -> int:
        with closing(self._connect()) as db:
            return db.execute("SELECT COUNT(*) AS n FROM shares").fetchone()["n"]


# Where shares live in the blob store. A prefix rather than the root, so
# listing and counting cannot accidentally sweep up anything else.
BLOB_PREFIX = "shares"


class BlobShareStore:
    """The same three operations, backed by Vercel Blob.

    For running somewhere with no disk that survives a request, which is every
    serverless host. One object per share, at a path derived from its id, so
    reading one is a direct fetch rather than a scan.

    Stored private rather than public. The id is unguessable either way, but a
    private object cannot be read at all without the deployment's own
    credentials, and a share describes how a business runs.
    """

    def __init__(self, client=None, prefix: str = BLOB_PREFIX) -> None:
        self.prefix = prefix
        if client is not None:
            self._client = client
            return

        try:
            from vercel.blob import BlobClient
        except ImportError as exc:  # pragma: no cover - deployment-time problem
            raise RuntimeError(
                "Blob storage was selected but the vercel package is not "
                "installed. Run: pip install vercel"
            ) from exc

        self._client = BlobClient()

    def _path(self, share_id: str) -> str:
        return f"{self.prefix}/{share_id}.json"

    def create(self, payload: dict, title: str, ttl_days: int = TTL_DAYS) -> ShareRecord:
        record, _ = _new_record(payload, title, ttl_days)

        self._client.put(
            self._path(record.id),
            record.encoded().encode("utf-8"),
            access="private",
            content_type="application/json",
            # The id is the name. A random suffix would make the object
            # impossible to find again from the id alone.
            add_random_suffix=False,
        )
        return record

    def get(self, share_id: str) -> ShareRecord | None:
        # The id comes off a URL, so it is not allowed to describe a path.
        if not share_id or "/" in share_id or ".." in share_id:
            return None

        try:
            result = self._client.get(self._path(share_id), access="private")
        except Exception:
            # A missing object and a store having a bad day look the same to a
            # reader, and both mean "that link does not work". They are not the
            # same to us, so the difference goes in the log rather than being
            # thrown away. Swallowing an exception without a trace turns a
            # five-minute fix into an afternoon.
            log.exception("blob read failed for %s", share_id)
            return None

        if result is None:
            log.info("no blob for share %s", share_id)
            return None

        status = getattr(result, "status_code", 200)
        if status != 200:
            log.warning("blob read for %s returned status %s", share_id, status)
            return None

        try:
            record = ShareRecord.decoded(_read_all(result))
        except Exception:
            log.exception("blob content for %s could not be read back", share_id)
            return None

        if record.expires_at < datetime.now(timezone.utc):
            # Expired objects are removed as they are found, which is the same
            # sweep-on-read the SQLite store does. Nothing is scheduled, and
            # nothing has to be remembered.
            try:
                self._client.delete(self._path(share_id))
            except Exception:
                log.warning("could not sweep expired share %s", share_id, exc_info=True)
            return None

        return record

    def count(self) -> int:
        page = self._client.list_objects(prefix=f"{self.prefix}/", limit=1000)
        return len(page.blobs)


def _read_all(result) -> str:
    """Get the text out of a blob read, whichever shape the client returned.

    The synchronous client returns the whole body as `content` bytes. The
    asynchronous one streams it in chunks. The published examples are all
    asynchronous, so the streaming shape is the one that is easy to find and the
    wrong one for the client used here.

    That cost a deployment to discover, because the failure was silent: the
    fetch returned 200, the body was present, and the code went looking for it
    under a name it does not have. Both shapes are handled now, checked in the
    order of how likely they are rather than how well documented.
    """

    content = getattr(result, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return bytes(content).decode("utf-8")
    if isinstance(content, str):
        return content

    stream = getattr(result, "stream", None)
    if isinstance(stream, (bytes, bytearray)):
        return bytes(stream).decode("utf-8")
    if stream is not None:
        return b"".join(chunk for chunk in stream).decode("utf-8")

    raise ValueError(
        "the blob read returned 200 but no body under 'content' or 'stream'; "
        f"the result was a {type(result).__name__}"
    )


def open_store(path: Path | None = None) -> ShareStorage:
    """Pick a store from the environment rather than from an argument.

    Vercel injects BLOB_READ_WRITE_TOKEN when a blob store is attached to the
    project, so its presence is a reliable signal that we are deployed and that
    there is somewhere to write. Everywhere else gets SQLite and a file.
    """

    if os.environ.get("BLOB_READ_WRITE_TOKEN") or os.environ.get("BLOB_STORE_ID"):
        return BlobShareStore()
    return ShareStore(path)
