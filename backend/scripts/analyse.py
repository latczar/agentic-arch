"""Run a process description through the extractor and print what came back.

    python scripts/analyse.py
    python scripts/analyse.py "every friday I export the sales report and ..."
    python scripts/analyse.py --file process.txt

Shows each attempt, so you can watch the checker reject a graph and the model
fix it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.assess import AssessmentResult, assess_process  # noqa: E402
from app.export_n8n import to_n8n_json  # noqa: E402
from app.extract import ExtractionResult, extract_process  # noqa: E402
from app.llm.base import LLMError  # noqa: E402
from app.llm.gemini import GeminiClient  # noqa: E402
from app.llm.record import (  # noqa: E402
    DEFAULT_CASE,
    RecordingLLM,
    ReplayLLM,
    available_cases,
)
from app.schemas.assessment import AutomationPlan, Verdict  # noqa: E402
from app.schemas.process import ProcessGraph, StepKind  # noqa: E402

MARKER = {
    Verdict.FULLY_AUTOMATABLE: "[ auto  ]",
    Verdict.AUTOMATABLE_WITH_CONTROL: "[ guard ]",
    Verdict.HUMAN_REQUIRED: "[  you  ]",
    Verdict.NEEDS_MORE_INFO: "[  ???  ]",
}

EXAMPLE = (
    "Every morning I go through my emails looking for invoices. When I find one "
    "I download the PDF attachment, read the total off it, and type that into "
    "our Google Sheet. Then I message accounting on Slack to say it's in. If "
    "it's a big one, over five thousand pounds, I check with my manager first "
    "before I put it through."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("description", nargs="?", help="The process, in plain English.")
    parser.add_argument("--file", type=Path, help="Read the description from a file.")
    parser.add_argument("--attempts", type=int, default=3, help="Max tries before giving up.")
    parser.add_argument("--model", help="Override the Gemini model.")
    parser.add_argument(
        "--export-n8n",
        type=Path,
        metavar="FILE",
        help="Also write an importable n8n workflow scaffold to this path.",
    )
    parser.add_argument(
        "--replay",
        nargs="?",
        const=DEFAULT_CASE,
        metavar="CASE",
        help=(
            "Replay a saved case instead of calling the API. No key, no quota. "
            f"Cases: {', '.join(available_cases()) or 'none recorded'}."
        ),
    )
    args = parser.parse_args()

    if args.file:
        description = args.file.read_text(encoding="utf-8")
    elif args.description:
        description = args.description
    else:
        description = EXAMPLE
        print("No description given, using the built-in example.\n")

    try:
        if args.replay:
            llm = ReplayLLM(args.replay)
        else:
            # Every real call is recorded, so the next run can be free.
            llm = RecordingLLM(
                GeminiClient(model=args.model) if args.model else GeminiClient()
            )
    except LLMError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    print(f"Asking {llm.name}...\n")

    print("Step 1 of 2: mapping the process")
    try:
        extraction = extract_process(description, llm, max_attempts=args.attempts)
    except LLMError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    report(extraction)
    if extraction.graph is None:
        return 1

    show(extraction.graph)

    print("\nStep 2 of 2: judging what can be automated")
    try:
        assessment = assess_process(extraction.graph, llm, max_attempts=args.attempts)
    except LLMError as exc:
        # A failure here still leaves a useful process map on screen.
        print(f"  skipped: {exc}", file=sys.stderr)
        return 1

    report(assessment)
    if assessment.plan is None:
        return 1

    show_plan(assessment.plan)

    if args.export_n8n:
        args.export_n8n.write_text(
            to_n8n_json(extraction.graph, assessment.plan), encoding="utf-8"
        )
        print(f"\nWrote {args.export_n8n}. Import it from n8n's Workflows > Import from File.")
        print("The integration nodes are placeholders; each one says what to replace it with.")

    return 0


def report(result: ExtractionResult | AssessmentResult) -> None:
    """Print what each attempt did, so repairs are visible rather than hidden."""

    for attempt in result.attempts:
        if attempt.ok:
            print(f"  attempt {attempt.number}: accepted")
        else:
            print(f"  attempt {attempt.number}: rejected, {len(attempt.errors)} problem(s)")
            for error in attempt.errors:
                print(f"      - {error}")
    print()

    if not result.ok:
        print("  gave up: the model could not produce something valid.\n")


def show(graph: ProcessGraph) -> None:
    print(f"{graph.title.upper()}")
    print(f"{graph.summary}\n")
    print(f"Trigger: {graph.trigger.description} ({graph.trigger.kind.value})")
    if graph.systems:
        print("Systems: " + ", ".join(s.name for s in graph.systems))
    print()

    for step in graph.steps:
        marker = "?" if step.kind is StepKind.DECISION else " "
        print(f"  [{marker}] {step.id}  ({step.kind.value})")
        print(f"      {step.name}")
        if step.iterates_over:
            print(f"      repeats for: {step.iterates_over}")
        if step.assumption:
            print(f"      assumed: {step.assumption}")
        for edge in graph.outgoing(step.id):
            label = f" if {edge.condition}" if edge.condition else ""
            print(f"      -> {edge.to_step}{label}")
        print()

    if graph.questions:
        print("Worth asking before building anything:")
        for question in graph.questions:
            print(f"  - {question.question}")
            print(f"    why: {question.why_it_matters}")
            if question.suggested_answers:
                print(f"    e.g. {' / '.join(question.suggested_answers)}")


def show_plan(plan: AutomationPlan) -> None:
    print(f"\n{plan.headline}\n")

    for assessment in plan.assessments:
        print(f"{MARKER[assessment.verdict]}  {assessment.step_id}")
        print(f"            {assessment.rationale}")

        if assessment.risks:
            print(f"            risks: {', '.join(r.value for r in assessment.risks)}")

        # Where we overruled the model, say so. A reader cannot otherwise tell a
        # sensible verdict apart from one that was caught and corrected.
        for override in assessment.overrides:
            print(f"            ! we changed this: {override.was} -> {override.now}")
            print(f"              {override.because}")

        for control in assessment.controls:
            limit = ""
            if control.threshold:
                t = control.threshold
                currency = f" {t.currency}" if t.currency else ""
                limit = f" when {t.field} {t.operator.value} {t.value}{currency}"
            print(f"            -> {control.kind.value}{limit}")
            print(f"               {control.reason}")
            if control.who_approves:
                print(f"               approver: {control.who_approves}")

        for blocker in assessment.blockers:
            print(f"            blocked by {blocker.kind.value}: {blocker.detail}")

        print()

    counts = plan.counts()
    print(
        f"Runs on its own: {counts[Verdict.FULLY_AUTOMATABLE]}   "
        f"Needs a guard: {counts[Verdict.AUTOMATABLE_WITH_CONTROL]}   "
        f"Stays with you: {counts[Verdict.HUMAN_REQUIRED]}   "
        f"Unclear: {counts[Verdict.NEEDS_MORE_INFO]}"
    )
    if plan.biggest_win:
        print(f"Start with: {plan.biggest_win}")


if __name__ == "__main__":
    raise SystemExit(main())
