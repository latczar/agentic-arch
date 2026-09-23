"""Embed every playbook once and commit the result.

Build-time work, not request-time work. The corpus changes when somebody writes
an article, which is rarely, so paying for it on every cold start would be
paying repeatedly for an answer that does not change.

    python scripts/embed_playbooks.py

Writes playbooks/vectors.json, which is committed. Rerun it after editing any
article, and the run says plainly if what is on disk no longer matches.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.embed import DIMENSIONS, MODEL, embed  # noqa: E402
from app.playbooks import VECTOR_FILE, load_playbooks  # noqa: E402


def fingerprint(text: str) -> str:
    """So a stale vector file announces itself instead of silently misleading."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    playbooks = load_playbooks()
    if not playbooks:
        print("No playbooks found.")
        return 1

    print(f"Embedding {len(playbooks)} playbooks with {MODEL} at {DIMENSIONS} dimensions.\n")

    vectors: dict[str, list[float]] = {}
    fingerprints: dict[str, str] = {}

    for playbook in playbooks:
        text = playbook.document
        vectors[playbook.id] = embed(text)
        fingerprints[playbook.id] = fingerprint(text)
        print(f"  {playbook.id:26s} {len(text):5d} chars -> {len(vectors[playbook.id])} dims")

    VECTOR_FILE.write_text(
        json.dumps(
            {
                "model": MODEL,
                "dimensions": DIMENSIONS,
                "fingerprints": fingerprints,
                "vectors": vectors,
            },
            indent=1,
        ),
        encoding="utf-8",
    )

    size = VECTOR_FILE.stat().st_size / 1024
    print(f"\nWrote {VECTOR_FILE.name}, {size:.0f}KB.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
