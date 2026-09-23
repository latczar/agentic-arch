"""The bake-off's arithmetic, and the parts of it that could quietly lie.

A comparison is only as good as its bookkeeping. Counting thinking tokens twice,
crediting a failed call with the last call's usage, or ranking a fast wrong
model above a slow right one would all produce a confident table that is wrong.
None of these reach the network.
"""

from types import SimpleNamespace

import pytest

from app.llm.base import LLMError
from app.llm.gemini import GeminiClient
from evals.bakeoff import Metered, billed_output, cost, run_model, table
from evals.runner import Report


def test_billed_output_uses_the_total_when_it_is_reported():
    """Right whether or not the provider's "output" already includes thinking."""

    excludes = {"input": 100, "output": 50, "thinking": 30, "total": 180}
    includes = {"input": 100, "output": 80, "thinking": 30, "total": 180}

    assert billed_output(excludes) == 80
    assert billed_output(includes) == 80


def test_billed_output_adds_thinking_when_there_is_no_total():
    assert billed_output({"input": 100, "output": 50, "thinking": 30}) == 80


def test_cost_at_a_known_price():
    tokens = {"input": 1_000_000, "output": 1_000_000, "thinking": 0, "total": 2_000_000}
    assert cost("gemini-3.5-flash-lite", tokens) == pytest.approx(0.30 + 2.50)


def test_cost_is_unknown_rather_than_zero_for_an_unpriced_model():
    """Zero would read as free, which is a different and wrong claim."""

    assert cost("some-model-nobody-priced", {"input": 10, "output": 10}) is None


class FakeClient:
    name = "fake:model"

    def __init__(self, replies):
        self.replies = list(replies)
        self.last_usage = {}

    def generate_json(self, **_):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        text, usage = reply
        self.last_usage = usage
        return text


def test_the_meter_counts_calls_and_adds_up_tokens():
    inner = FakeClient([
        ("{}", {"input": 10, "output": 5, "thinking": 2, "total": 17}),
        ("{}", {"input": 20, "output": 5, "thinking": 0, "total": 25}),
    ])
    meter = Metered(inner)
    meter.generate_json(system="s", prompt="p", schema={})
    meter.generate_json(system="s", prompt="p", schema={})

    assert meter.calls == 2
    assert meter.tokens == {"input": 30, "output": 10, "thinking": 2, "total": 42}


def test_a_failed_call_is_not_credited_with_the_last_calls_tokens():
    inner = FakeClient([
        ("{}", {"input": 10, "output": 5, "thinking": 0, "total": 15}),
        LLMError("busy"),
    ])
    meter = Metered(inner)
    meter.generate_json(system="s", prompt="p", schema={})
    with pytest.raises(LLMError):
        meter.generate_json(system="s", prompt="p", schema={})

    assert meter.calls == 2
    assert meter.tokens["input"] == 10


def summary(model, passed, total, seconds, could_not_run=0):
    return {
        "model": model, "passed": passed, "total": total, "could_not_run": could_not_run,
        "seconds": seconds, "calls": 2.0,
        "tokens": {"input": 1, "output": 1, "thinking": 0, "total": 2},
        "cost_per_analysis": 0.001,
    }


def test_the_table_ranks_judgement_before_speed():
    """A fast model that gets the judgement wrong is not a candidate at any price."""

    rendered = table([
        summary("fast-but-wrong", 20, 26, 5.0),
        summary("slow-but-right", 26, 26, 30.0),
        summary("right-and-quicker", 26, 26, 12.0),
    ])
    order = [line.split("|")[1].strip() for line in rendered.splitlines()[2:]]

    assert order == ["right-and-quicker", "slow-but-right", "fast-but-wrong"]


def test_a_model_that_never_ran_says_so():
    rendered = table([summary("out-of-quota", 0, 0, None, could_not_run=1)])
    assert "none ran" in rendered


def test_a_used_up_allowance_stops_that_model_early():
    """Four more identical failures would tell nobody anything."""

    class Exhausted:
        name = "gemini:exhausted"
        last_usage = {}

        def generate_json(self, **_):
            raise LLMError("Today's free model allowance has been used up.")

    run = run_model("exhausted", lambda _: Exhausted(), pause=0, say=lambda *_: None)

    assert len(run.cases) == 1
    assert run.summary()["could_not_run"] == 1


def test_a_live_baseline_records_which_model_produced_it():
    assert Report(mode="live", model="gemini:x").to_baseline()["model"] == "gemini:x"
    assert "model" not in Report(mode="guards").to_baseline()


def test_the_client_keeps_the_token_counts_the_provider_reports():
    """What the bake-off reads. Nothing else depends on it."""

    reply = SimpleNamespace(
        output_text="{}",
        usage=SimpleNamespace(
            total_input_tokens=120, total_output_tokens=40,
            total_thought_tokens=15, total_tokens=175,
        ),
    )
    c = GeminiClient(api_key="not-a-real-key")
    c._client = SimpleNamespace(interactions=SimpleNamespace(create=lambda **_: reply))

    assert c.generate_json(system="s", prompt="p", schema={}) == "{}"
    assert c.last_usage == {"input": 120, "output": 40, "thinking": 15, "total": 175}
