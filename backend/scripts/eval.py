"""Score the thing, so a change can be measured rather than hoped about.

    python scripts/eval.py                 the safety net, free and instant
    python scripts/eval.py replay          the whole pipeline, recorded answers
    python scripts/eval.py live            the whole pipeline, real model
    python scripts/eval.py guards --save   write the result as the new baseline

Exits non zero on a failure or a regression, so it can sit in a build.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm.base import LLMError  # noqa: E402
from app.llm.gemini import GeminiClient  # noqa: E402
from app.playbooks import EmbeddingRetriever, LexicalRetriever  # noqa: E402
from evals.cases import PIPELINE_CASES, SHOULD_FIRE, SHOULD_STAY_QUIET  # noqa: E402
from evals.playbook_cases import SHOULD_FIND_NOTHING, SHOULD_MATCH  # noqa: E402
from evals.runner import (  # noqa: E402
    Report,
    load_baseline,
    run_guards,
    run_live,
    run_playbooks,
    run_replay,
    save_baseline,
)

TICK = "pass"
CROSS = "FAIL"
GAP = "gap "   # Known, written down, not breaking the build.
FIXED = "NEW "  # A known gap that started passing. Go and delete the excuse.


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        nargs="?",
        default="guards",
        choices=["guards", "replay", "playbooks", "live", "all"],
        help="What to measure. Default is the free one.",
    )
    parser.add_argument("--save", action="store_true", help="Record this run as the baseline.")
    parser.add_argument("--model", help="Override the model, live mode only.")
    parser.add_argument(
        "--embeddings",
        action="store_true",
        help="Also score the embedding retriever. Needs a key and the built vectors.",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Totals only, no per check detail."
    )
    args = parser.parse_args()

    reports: list[Report] = []

    if args.mode in ("guards", "all"):
        reports.append(run_guards())

    if args.mode in ("replay", "all"):
        reports.append(run_replay())

    if args.mode in ("playbooks", "all"):
        # The word matcher always. It needs no key, no network and no build
        # step, so it belongs in the run everybody can do for nothing.
        reports.append(run_playbooks(LexicalRetriever()))

        if args.embeddings:
            # Opt in, because this one costs a call per case and will not run
            # at all on a fresh clone. Kept out of "all" so that "all" stays
            # free and offline, which is the only reason anybody runs it often.
            try:
                from app.embed import embed

                reports.append(run_playbooks(EmbeddingRetriever(embed_query=embed)))
            except LLMError as exc:
                print(f"Skipping the embedding retriever: {exc}\n", file=sys.stderr)

    if args.mode == "live":
        try:
            make = lambda: GeminiClient(model=args.model) if args.model else GeminiClient()
            make()  # Fail now, with a clear message, rather than case by case.
        except LLMError as exc:
            print(f"{exc}", file=sys.stderr)
            return 1
        print(
            f"Running {len(PIPELINE_CASES)} cases against a live model. "
            "Two calls each, with a pause between, so this takes a minute.\n"
        )
        reports.append(run_live(make))

    failed = False
    for report in reports:
        if show(report, quiet=args.quiet):
            failed = True
        if args.save:
            path = save_baseline(report)
            print(f"  baseline written to {path.name}\n")

    return 1 if failed else 0


def show(report: Report, quiet: bool = False) -> bool:
    """Print a report. Returns True if anything is wrong."""

    print(f"=== {report.mode} " + "=" * (60 - len(report.mode)))
    print()

    for result in report.results:
        if result.error:
            print(f"  {CROSS}  {result.case_id}")
            print(f"        could not run: {result.error}")
            continue

        if result.known_gap and not result.ok:
            mark = GAP
        elif result.fixed:
            mark = FIXED
        else:
            mark = TICK if result.ok else CROSS

        print(f"  {mark}  {result.case_id}  ({result.passed}/{result.total})")

        for outcome in result.outcomes:
            if outcome.passed and (quiet or result.ok):
                continue
            flag = " " if outcome.passed else ">"
            print(f"      {flag} {outcome.name}")
            print(f"        {outcome.detail}")

        if result.known_gap and not result.ok:
            print(f"        known: {result.known_gap}")

    print()
    print(f"  {report.passed}/{report.total} checks, {report.score:.0%}")
    if report.gaps:
        print(f"  {len(report.gaps)} of those are gaps we already knew about")

    if report.mode == "guards":
        # Precision and recall, in the only words that matter here. A net that
        # catches everything by flagging everything is not a net.
        fire_ids = {c.id for c in SHOULD_FIRE}
        caught = sum(1 for r in report.results if r.case_id in fire_ids and r.ok)
        quiet_ids = {c.id for c in SHOULD_STAY_QUIET}
        calm = sum(1 for r in report.results if r.case_id in quiet_ids and r.ok)
        print(f"  caught {caught}/{len(fire_ids)} real risks")
        print(f"  stayed quiet on {calm}/{len(quiet_ids)} harmless steps")

    if report.mode.startswith("playbooks"):
        matched = {c.query[:58] for c in SHOULD_MATCH}
        right = sum(1 for r in report.results if r.case_id in matched and r.ok)
        nothing = {c.query[:58] for c in SHOULD_FIND_NOTHING}
        calm = sum(1 for r in report.results if r.case_id in nothing and r.ok)
        print(f"  found the right article {right}/{len(matched)} times")
        print(f"  correctly found nothing {calm}/{len(nothing)} times")

    for result in report.fixed:
        print(f"  FIXED:  {result.case_id} was a known gap and now passes")

    baseline = load_baseline(report.mode)
    if baseline is None:
        print("  no baseline yet. Run with --save once you are happy with this.")
        print()
        return bool(report.broken or report.surprises)

    regressions, improvements = report.compare(baseline)
    was = baseline.get("score", 0)
    print(f"  baseline was {was:.0%}, recorded {baseline.get('recorded_at', 'at some point')}")

    recorded_on = baseline.get("model")
    if recorded_on and report.model and recorded_on != report.model:
        # Still compared, because that is often the question being asked. But
        # said out loud, so a difference is not read as a change to the code.
        print(f"  note: the baseline came from {recorded_on}, this run from {report.model},")
        print("        so any difference may be the model rather than the code")

    for line in improvements:
        print(f"  better: {line}")
    for line in regressions:
        print(f"  WORSE:  {line}")
    print()

    # A known gap failing is not a build failure. Anything else is.
    return bool(regressions or report.broken or report.surprises)


if __name__ == "__main__":
    raise SystemExit(main())
