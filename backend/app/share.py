"""Storage for shared analyses, so a result can be sent to somebody else.

SQLite, because this is a handful of small rows and adding a database server to
the deployment would cost more than it is worth. It is also the boring choice
that works everywhere without configuration.

Three things this has to get right, and they are all about the fact that a
shared analysis describes how somebody's business actually runs:

  - The identifier is unguessable. Sequential ids would let anyone walk the
    whole table and read every process anybody has ever analysed.
  - Shares expire. An unlisted link that lives forever is a slow leak, and
    nobody comes back to tidy up.
  - Size is capped. A public write endpoint with no limit is somebody else's
    free storage.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

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
