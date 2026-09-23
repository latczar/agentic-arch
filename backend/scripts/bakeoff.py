"""Compare models on the live cases: checks passed, time, calls and cost.

    python scripts/bakeoff.py gemini-3.5-flash-lite gemini-3.8-flash
    python scripts/bakeoff.py --table

Each model named is run and saved to evals/bakeoff/<model>.json, replacing any
earlier result for it. --table prints every saved result as one Markdown table.

--ipv4 is for machines where IPv6 is advertised but broken. Python then waits
about 20 seconds on every new connection before falling back, which curl does
not, so it hides well. Every case opens a fresh client, so without the flag it
adds 20 seconds to each timing and the comparison measures the network.
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm.gemini import GeminiClient  # noqa: E402
from evals.bakeoff import load_all, run_model, save, table  # noqa: E402


def prefer_ipv4() -> None:
    original = socket.getaddrinfo

    def ipv4_only(*args, **kwargs):
        found = original(*args, **kwargs)
        return [entry for entry in found if entry[0] == socket.AF_INET] or found

    socket.getaddrinfo = ipv4_only


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="*", help="Model names to run, e.g. gemini-3.5-flash-lite")
    parser.add_argument("--table", action="store_true", help="Print every saved result as a table.")
    parser.add_argument("--ipv4", action="store_true", help="Connect over IPv4 only. See above.")
    args = parser.parse_args()

    if not args.models and not args.table:
        parser.print_help()
        return 1

    if args.ipv4:
        prefer_ipv4()

    for model in args.models:
        print(f"{model}")
        run = run_model(model, lambda name: GeminiClient(model=name))
        path = save(run)
        s = run.summary()
        median = f"median {s['seconds']}s" if s["seconds"] is not None else "no timings"
        print(f"  {s['passed']} of {s['total']} checks, {s['could_not_run']} could not run, "
              f"{median}, saved to {path.name}\n")

    if args.table or args.models:
        print(table(load_all()))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
