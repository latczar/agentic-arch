"""Record real model responses to disk, and replay them without a network call.

This is the seam earning its keep. Both classes below satisfy the same
StructuredLLM protocol as the real client, so anything that takes a model takes
these too, with no changes.

Why it matters here: the free tier is metered per day. Re-running the same
description to work on the printing code is a waste of a scarce resource, and it
makes development slower and less predictable. Record once, replay for free.

The same trick is standard practice well beyond free tiers. It is how you get
tests that do not cost money, do not need the network, and give the same answer
every time.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.llm.base import LLMError, StructuredLLM

log = logging.getLogger(__name__)

RECORDINGS = Path(__file__).resolve().parents[2] / "recordings"

# Ad-hoc runs land here and are gitignored. The named folders beside it are
# curated demo cases, kept in the repo so the project runs without a key.
SCRATCH_DIR = RECORDINGS / "_latest"
DEFAULT_CASE = "invoice-with-approval"


def available_cases() -> list[str]:
    """Names of the curated demo cases in the repository."""

    if not RECORDINGS.exists():
        return []
    return sorted(
        d.name for d in RECORDINGS.iterdir() if d.is_dir() and not d.name.startswith("_")
    )


class RecordingLLM:
    """Passes calls through to a real client, saving each response."""

    def __init__(self, inner: StructuredLLM, directory: Path | None = None) -> None:
        self.inner = inner
        self.name = f"recording({inner.name})"
        self.directory = directory or SCRATCH_DIR

        # Keeping notes is not the job. Somewhere with a read-only disk, which
        # is every serverless host, this used to raise before the model was ever
        # called and turned a working request into a 500. Recording is a
        # convenience; convenience does not get to fail the thing it assists.
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.recording = True
        except OSError:
            log.info("cannot write to %s, passing calls through unrecorded", self.directory)
            self.recording = False

    def generate_json(self, *, system: str, prompt: str, schema: dict) -> str:
        text = self.inner.generate_json(system=system, prompt=prompt, schema=schema)
        if not self.recording:
            return text

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        path = self.directory / f"{stamp}.json"
        try:
            path.write_text(
                json.dumps(
                    {
                        "model": self.inner.name,
                        "recorded_at": datetime.now(timezone.utc).isoformat(),
                        "prompt": prompt,
                        "response": text,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            # The answer is in hand. Losing the copy of it is not worth losing
            # the answer over.
            log.warning("could not save a recording to %s", path, exc_info=True)

        return text


class ReplayLLM:
    """Returns previously recorded responses, in the order they were recorded.

    Deliberately does not try to match a prompt to a recording. Matching sounds
    cleverer but goes wrong quietly. A near miss silently replays the wrong
    answer. In order, and loudly when it runs out, is easier to trust.
    """

    def __init__(self, case: str | None = None, directory: Path | None = None) -> None:
        self.case = case or DEFAULT_CASE
        self.directory = directory or (RECORDINGS / self.case)
        self.name = f"replay:{self.case}"
        self._responses = self._load()
        self._position = 0

    def _load(self) -> list[str]:
        if not self.directory.exists():
            known = ", ".join(available_cases()) or "none"
            raise LLMError(
                f"No recorded case called '{self.case}'. Available: {known}."
            )
        responses = []
        for path in sorted(self.directory.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            responses.append(data["response"])
        if not responses:
            raise LLMError(f"No recordings in {self.directory}.")
        return responses

    def generate_json(self, *, system: str, prompt: str, schema: dict) -> str:
        if self._position >= len(self._responses):
            raise LLMError(
                f"Ran out of recordings after {len(self._responses)}. "
                "Record another run against a real model."
            )
        text = self._responses[self._position]
        self._position += 1
        return text
