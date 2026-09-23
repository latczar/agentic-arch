"""Running the cases, scoring them, and comparing against last time.

A score on its own answers almost nothing. "Thirty eight out of forty" is only
useful next to the thirty six you got before the change, which is why the
baseline lives in the repository and gets committed like anything else.

Three modes, and the difference between them is worth being straight about:

  guards   Our own code, no model. Free, instant, identical every time. Safe to
           run on every commit.

  replay   The whole pipeline against responses recorded earlier. Free, and it
           catches the day we change a schema and quietly stop being able to
           read an answer that used to be fine. It cannot tell you whether a
           prompt got better, because the recorded answer was produced by the
           old prompt and will keep coming back whatever you write.

  live     The whole pipeline against the real model. Costs quota, gives a
           slightly different answer each run, and is the only mode that can
           tell you a prompt change was an improvement.

Pretending replay measures prompt quality would be the comfortable lie here. It
measures whether our code still handles a known good answer, which is a
different and smaller thing.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.assess import assess_process
from app.extract import extract_process
from app.llm.base import LLMError, StructuredLLM
from app.llm.record import ReplayLLM
from app.schemas.assessment import mandatory_risks
from evals.cases import (
    GUARD_CASES,
    PIPELINE_CASES,
    GuardCase,
    PipelineCase,
    replayable_cases,
)
from evals.checks import Outcome
from evals.playbook_cases import ALL_CASES as PLAYBOOK_CASES
from evals.playbook_cases import PlaybookCase

# One baseline per mode. They measure different things and a live score is not
# comparable to a replay score, so keeping them in one file would only invite
# somebody to compare the two.
BASELINES = Path(__file__).resolve().parent / "baselines"

# The free tier is metered per minute as well as per day, and a live run fires
# two calls per case back to back. Without a breather the last few cases fail on
# rate limiting and look like quality regressions, which is the most misleading
# possible way for an eval to be wrong.
LIVE_PAUSE_SECONDS = 4.0


@dataclass
class CaseResult:
    case_id: str
    outcomes: list[Outcome] = field(default_factory=list)
    # Set when the case could not be run at all, which is different from running
    # and scoring badly, and is reported differently.
    error: str | None = None
    # Why we already knew this would fail, if we did.
    known_gap: str = ""

    @property
    def passed(self) -> int:
        return sum(1 for o in self.outcomes if o.passed)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def ok(self) -> bool:
        return self.error is None and self.passed == self.total

    @property
    def fixed(self) -> bool:
        """A known gap that has started passing. Worth saying out loud."""

        return bool(self.known_gap) and self.ok


@dataclass
class Report:
    mode: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.results)

    @property
    def total(self) -> int:
        return sum(r.total for r in self.results)

    @property
    def score(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def broken(self) -> list[CaseResult]:
        return [r for r in self.results if r.error]

    @property
    def gaps(self) -> list[CaseResult]:
        """Failing exactly as we said they would. Reported, not alarming."""

        return [r for r in self.results if r.known_gap and not r.ok and not r.error]

    @property
    def fixed(self) -> list[CaseResult]:
        return [r for r in self.results if r.fixed]

    @property
    def surprises(self) -> list[CaseResult]:
        """Failures nobody wrote down in advance. The ones that matter."""

        return [r for r in self.results if not r.known_gap and not r.ok and not r.error]

    def to_baseline(self) -> dict:
        return {
            "mode": self.mode,
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "score": round(self.score, 4),
            "passed": self.passed,
            "total": self.total,
            # Per case as well as overall, because "we went from 38 to 37" is a
            # shrug and "the deposit case lost a check" is somewhere to look.
            "cases": {
                r.case_id: {"passed": r.passed, "total": r.total} for r in self.results
            },
        }

    def compare(self, baseline: dict) -> tuple[list[str], list[str]]:
        """Return (regressions, improvements) against a saved baseline."""

        previous = baseline.get("cases", {})
        regressions: list[str] = []
        improvements: list[str] = []

        for result in self.results:
            was = previous.get(result.case_id)
            if was is None:
                improvements.append(f"{result.case_id}: new case, {result.passed}/{result.total}")
                continue
            if result.passed < was["passed"]:
                regressions.append(
                    f"{result.case_id}: {was['passed']}/{was['total']} "
                    f"before, {result.passed}/{result.total} now"
                )
            elif result.passed > was["passed"]:
                improvements.append(
                    f"{result.case_id}: {was['passed']}/{was['total']} "
                    f"before, {result.passed}/{result.total} now"
                )

        for case_id in previous:
            if not any(r.case_id == case_id for r in self.results):
                regressions.append(f"{case_id}: was in the baseline and did not run")

        return regressions, improvements


# --- The guard suite ----------------------------------------------------------


def grade_guard(case: GuardCase) -> CaseResult:
    found = mandatory_risks(case.step_name, case.description, case.kind)
    missed = sorted(f.value for f in case.expect - found)
    spurious = sorted(f.value for f in found - case.expect)

    if missed and spurious:
        detail = f"missed {missed}, and wrongly added {spurious}"
    elif missed:
        detail = f"missed {missed}"
    elif spurious:
        detail = f"wrongly flagged {spurious}"
    else:
        detail = f"exactly {sorted(f.value for f in found) or 'nothing'}, as expected"

    return CaseResult(
        case_id=case.id,
        outcomes=[Outcome(name=case.step_name, passed=not (missed or spurious), detail=detail)],
        known_gap=case.known_gap,
    )


def run_guards() -> Report:
    return Report(mode="guards", results=[grade_guard(c) for c in GUARD_CASES])


# --- The retrieval suite ------------------------------------------------------


def grade_playbook(case: PlaybookCase, retriever) -> CaseResult:
    """Did it find the right article, and did it keep quiet when there is none?

    Graded on the top result only. Somebody reads one article, not three, and a
    system that puts the right one second is wrong in the way that matters.
    """

    found = retriever.search(case.query, limit=2)
    top = found[0] if found else None
    got = top.playbook.id if top else None

    if case.expects is None:
        passed = got is None
        detail = (
            "found nothing, as it should"
            if passed
            else f"offered {got} at {top.score:.2f} for work the corpus does not cover"
        )
    elif got is None:
        passed = False
        detail = f"found nothing, wanted {case.expects}"
    elif got == case.expects:
        passed = True
        runner_up = f", next was {found[1].playbook.id} at {found[1].score:.2f}" if len(found) > 1 else ""
        detail = f"{got} at {top.score:.2f}{runner_up}"
    else:
        passed = False
        detail = f"offered {got} at {top.score:.2f}, wanted {case.expects}"

    return CaseResult(
        case_id=case.query[:58],
        outcomes=[Outcome(name=case.why, passed=passed, detail=detail)],
    )


def run_playbooks(retriever) -> Report:
    """Score one retriever over the labelled set.

    The mode carries the retriever's name, so the two get their own baselines
    and a change to one cannot be hidden by the other moving the other way.
    """

    return Report(
        mode=f"playbooks-{retriever.name}",
        results=[grade_playbook(c, retriever) for c in PLAYBOOK_CASES],
    )


# --- The pipeline suite -------------------------------------------------------


def run_case(case: PipelineCase, llm: StructuredLLM) -> CaseResult:
    result = CaseResult(case_id=case.id)

    try:
        extraction = extract_process(case.description, llm)
    except LLMError as exc:
        result.error = f"could not reach the model: {exc}"
        return result

    if extraction.graph is None:
        # Worth separating from a low score. The pipeline did not produce an
        # answer at all, so there was nothing to grade.
        faults = extraction.attempts[-1].errors if extraction.attempts else []
        result.error = "the map never validated: " + "; ".join(faults[:3] or ["no detail"])
        return result

    try:
        assessment = assess_process(extraction.graph, llm)
    except LLMError as exc:
        result.error = f"mapped fine, then could not reach the model: {exc}"
        return result

    if assessment.plan is None:
        faults = assessment.attempts[-1].errors if assessment.attempts else []
        result.error = "the judgement never validated: " + "; ".join(faults[:3] or ["no detail"])
        return result

    result.outcomes = [
        check.run(extraction.graph, assessment.plan) for check in case.checks
    ]
    return result


def run_replay() -> Report:
    results = []
    for case in replayable_cases():
        try:
            # A fresh player per case. Replays come back in the order they were
            # recorded, so a shared one would hand case two the answers to case
            # one and produce a confidently wrong score.
            llm: StructuredLLM = ReplayLLM(case.replay)
        except LLMError as exc:
            results.append(CaseResult(case_id=case.id, error=str(exc)))
            continue
        results.append(run_case(case, llm))
    return Report(mode="replay", results=results)


def run_live(make_llm, pause: float = LIVE_PAUSE_SECONDS) -> Report:
    results = []
    for index, case in enumerate(PIPELINE_CASES):
        if index and pause:
            time.sleep(pause)
        results.append(run_case(case, make_llm()))
    return Report(mode="live", results=results)


# --- Baselines ----------------------------------------------------------------


def baseline_path(mode: str, root: Path = BASELINES) -> Path:
    return root / f"{mode}.json"


def load_baseline(mode: str, root: Path = BASELINES) -> dict | None:
    path = baseline_path(mode, root)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_baseline(report: Report, root: Path = BASELINES) -> Path:
    path = baseline_path(report.mode, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_baseline(), indent=2) + "\n", encoding="utf-8")
    return path
