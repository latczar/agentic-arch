"""The same live cases on several models, compared on what matters here.

A live eval answers "is this model good enough". A bake-off answers "which
model", and that needs more than the score: how long a person waits, how many
calls it takes, and what it would cost once the free tier runs out. All four
come from one run, so they are measured on the same cases on the same evening
rather than stitched together from different days.

Results are saved one file per model, so a model can be added later without
rerunning the rest. Flash, for instance, could only join after its daily
allowance reset.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from evals.cases import PIPELINE_CASES
from evals.runner import LIVE_PAUSE_SECONDS, run_case

RESULTS = Path(__file__).resolve().parent / "bakeoff"

# Paid tier, standard rate, US dollars per million tokens: (input, output).
# Read from https://ai.google.dev/gemini-api/docs/pricing on 24 September 2026.
# The 3.6 to 3.8 Flash prices are promotional until 31 December 2026, and rise
# to 1.50 and 7.50 after that. Thinking is billed at the output rate.
#
# Written down rather than fetched, because a price that changes silently
# under a saved comparison makes the comparison meaningless. When the page
# changes, change this and the date together.
PRICES_AS_OF = "24 September 2026"
PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}


def billed_output(tokens: dict[str, int]) -> int:
    """Output tokens as billed: the answer plus any thinking.

    Providers disagree on whether "output" already includes thinking. Total
    minus input is right under either convention, so it is used whenever the
    total was reported, and the two counts are added only when it was not.
    """

    total = tokens.get("total", 0)
    if total:
        return max(total - tokens.get("input", 0), 0)
    return tokens.get("output", 0) + tokens.get("thinking", 0)


def cost(model: str, tokens: dict[str, int]) -> float | None:
    """Dollars for these tokens at the paid rate, or None if no price is known."""

    price = PRICES.get(model)
    if price is None:
        return None
    rate_in, rate_out = price
    return (tokens.get("input", 0) * rate_in + billed_output(tokens) * rate_out) / 1_000_000


class Metered:
    """A client with a stopwatch and a token counter on every call.

    Satisfies the same interface as the client it wraps, so the pipeline runs
    through it unchanged and cannot tell it is being measured.
    """

    def __init__(self, inner) -> None:
        self.inner = inner
        self.name = inner.name
        self.calls = 0
        self.tokens: dict[str, int] = {"input": 0, "output": 0, "thinking": 0, "total": 0}

    def generate_json(self, **kwargs) -> str:
        # Cleared first, so a call that fails before reporting usage is not
        # credited with the previous call's tokens.
        self.inner.last_usage = {}
        try:
            return self.inner.generate_json(**kwargs)
        finally:
            self.calls += 1
            for key, value in (getattr(self.inner, "last_usage", None) or {}).items():
                self.tokens[key] = self.tokens.get(key, 0) + value


@dataclass
class CaseRun:
    case: str
    passed: int
    total: int
    seconds: float
    calls: int
    tokens: dict[str, int]
    error: str | None = None


@dataclass
class ModelRun:
    model: str
    cases: list[CaseRun] = field(default_factory=list)
    recorded_at: str = ""

    @property
    def ran(self) -> list[CaseRun]:
        return [c for c in self.cases if not c.error]

    def summary(self) -> dict:
        ran = self.ran
        tokens = {
            key: round(statistics.mean(c.tokens.get(key, 0) for c in ran)) if ran else 0
            for key in ("input", "output", "thinking", "total")
        }
        return {
            "model": self.model,
            "recorded_at": self.recorded_at,
            "passed": sum(c.passed for c in ran),
            "total": sum(c.total for c in ran),
            "could_not_run": len(self.cases) - len(ran),
            # The median, because one analysis that needed a repair round would
            # drag a mean somewhere no typical visitor ever waits.
            "seconds": round(statistics.median(c.seconds for c in ran), 1) if ran else None,
            "calls": round(statistics.mean(c.calls for c in ran), 1) if ran else None,
            "tokens": tokens,
            "cost_per_analysis": cost(self.model, tokens) if ran else None,
            "prices_as_of": PRICES_AS_OF,
            "first_error": next((c.error for c in self.cases if c.error), None),
        }

    def to_json(self) -> dict:
        return {
            "summary": self.summary(),
            "cases": [c.__dict__ for c in self.cases],
        }


def run_model(model: str, make_client, pause: float = LIVE_PAUSE_SECONDS, say=print) -> ModelRun:
    """Every live case on one model, each with a fresh client, as the live eval does."""

    run = ModelRun(
        model=model,
        recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    for index, case in enumerate(PIPELINE_CASES):
        if index and pause:
            time.sleep(pause)

        try:
            llm = Metered(make_client(model))
        except Exception as exc:  # a bad model name fails here, not in the call
            run.cases.append(CaseRun(case.id, 0, 0, 0.0, 0, {}, error=str(exc)))
            continue

        started = time.monotonic()
        result = run_case(case, llm)
        seconds = time.monotonic() - started

        run.cases.append(
            CaseRun(
                case=case.id,
                passed=result.passed,
                total=result.total,
                seconds=round(seconds, 1),
                calls=llm.calls,
                tokens=dict(llm.tokens),
                error=result.error,
            )
        )

        mark = "error" if result.error else f"{result.passed}/{result.total}"
        say(f"    {case.id:<24} {mark:<6} {seconds:5.1f}s  {llm.calls} calls")

        # A used-up daily allowance fails every remaining case identically.
        # Carrying on only prints the same sentence four more times.
        if result.error and "used up" in result.error:
            say("    daily allowance used up, skipping the rest of this model")
            break

    return run


def save(run: ModelRun, root: Path = RESULTS) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{run.model}.json"
    path.write_text(json.dumps(run.to_json(), indent=2) + "\n", encoding="utf-8")
    return path


def load_all(root: Path = RESULTS) -> list[dict]:
    if not root.is_dir():
        return []
    return [
        json.loads(path.read_text(encoding="utf-8"))["summary"]
        for path in sorted(root.glob("*.json"))
    ]


def table(summaries: list[dict]) -> str:
    """The saved results as a Markdown table, best first.

    Ordered by checks passed, then by time, because a fast model that gets the
    judgement wrong is not a candidate at any price.
    """

    def score(s: dict) -> float:
        return s["passed"] / s["total"] if s["total"] else -1.0

    rows = sorted(summaries, key=lambda s: (-score(s), s["seconds"] or 1e9))

    lines = [
        "| model | checks passed | could not run | time per analysis | calls | thinking tokens | cost per 1,000 analyses |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in rows:
        checks = f"{s['passed']} of {s['total']}" if s["total"] else "none ran"
        seconds = f"{s['seconds']}s" if s["seconds"] is not None else "n/a"
        calls = f"{s['calls']}" if s["calls"] is not None else "n/a"
        thinking = f"{s['tokens']['thinking']:,}" if s["total"] else "n/a"
        dollars = s["cost_per_analysis"]
        if not s["total"]:
            per_thousand = "n/a"
        elif dollars is None:
            per_thousand = "no price"
        else:
            per_thousand = f"${dollars * 1000:.2f}"
        lines.append(
            f"| {s['model']} | {checks} | {s['could_not_run']} | {seconds} | {calls} "
            f"| {thinking} | {per_thousand} |"
        )
    return "\n".join(lines)
