"""Deployment entrypoint.

Vercel looks for a FastAPI instance called `app` in one of a handful of file
names at the repository root. The application itself lives in backend/, and its
modules import each other as `app.something`, which only resolves when backend/
is on the path. Rather than rewrite every import to suit one host, this file
puts backend/ on the path and re-exports what is already there.

Six lines here beats a package rename that would have to be undone the first
time the project moves somewhere else.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app.api import app  # noqa: E402  (the path has to be set first)

__all__ = ["app"]
