"""Loop / cascade guard — AC-12 (§7.6), SPEC-11 §2.4.

Forces a state the supervisor can never advance — the classifier is stubbed to return without
filling its slot, so `supervisor → classifier → supervisor → …` would spin forever — and asserts the
run **terminates** with a degraded, escalated decision instead of looping.

Two independent layers are tested, as SPEC-11 requires:
  1. the explicit `step_count >= MAX_STEPS` check in `decide_next` / the supervisor node;
  2. LangGraph's `recursion_limit` as the backstop when (1) is effectively disabled.

No API key needed: intent classification, memory and MCP are stubbed; the graph itself is real.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

import src.graph as graph_mod  # noqa: E402
from src.config import settings  # noqa: E402
from src.models import Intent  # noqa: E402

CLAIM = json.loads((settings.sample_claims_dir / "claim_001.json").read_text(encoding="utf-8"))


class _NoMCP:
    available = False
    error = "stubbed for the loop test"


@pytest.fixture
def stuck_graph(monkeypatch):
    """A real compiled graph whose classifier never fills `classification`."""

    async def fixed_intent(_state):
        return Intent(kind="fnol_triage", confidence=1.0, rationale="stub")

    async def no_recall(*_a, **_k):
        return []

    async def no_remember(*_a, **_k):
        return None

    calls = {"classifier": 0}

    async def stuck_classifier(self, state):
        calls["classifier"] += 1
        return {"step_count": state.get("step_count", 0) + 1}  # slot never filled

    monkeypatch.setattr(graph_mod, "classify_intent", fixed_intent)
    monkeypatch.setattr(graph_mod, "recall", no_recall)
    monkeypatch.setattr(graph_mod, "remember", no_remember)
    monkeypatch.setattr(graph_mod, "build_checkpointer", MemorySaver)
    monkeypatch.setattr(graph_mod.TriageGraph, "classifier", stuck_classifier)

    tg = graph_mod.TriageGraph(mcp=_NoMCP())
    tg.build()
    return tg, calls


async def test_step_limit_stops_a_runaway_supervisor_loop(stuck_graph):
    tg, calls = stuck_graph
    decision = await graph_mod.run_claim(tg, CLAIM, run_id="run-looptest", thread_id="t-loop-1")

    # terminated, and by the explicit step guard (not an exception)
    assert decision.steps <= settings.max_steps + 3
    assert calls["classifier"] >= 3, "the loop must actually have been entered"
    assert calls["classifier"] < settings.max_steps
    # a degraded decision that goes to a human — never a silent pass
    assert decision.degraded is True
    assert any(e.get("kind") == "step_limit" for e in decision.errors)
    assert decision.routing is None or decision.routing.auto_approved is False
    assert "human" in decision.message.lower()


async def test_recursion_limit_is_the_backstop_when_the_step_guard_is_off(stuck_graph, monkeypatch):
    tg, calls = stuck_graph
    monkeypatch.setattr(settings, "max_steps", 10_000)  # disable layer 1

    from src.state import new_state

    with pytest.raises(Exception) as exc_info:
        await tg.app.ainvoke(
            new_state(run_id="run-looptest", thread_id="t-loop-2", raw_input=CLAIM),
            config={"configurable": {"thread_id": "t-loop-2"}, "recursion_limit": 12},
        )
    assert type(exc_info.value).__name__ == "GraphRecursionError"


async def test_run_claim_turns_a_recursion_error_into_a_degraded_decision(stuck_graph,
                                                                          monkeypatch):
    """NFR-04 — the CLI path never raises: a runaway loop still yields an escalated decision."""
    tg, _ = stuck_graph
    monkeypatch.setattr(settings, "max_steps", 6)  # recursion_limit = 12 inside run_claim
    # a supervisor with its step guard removed: it keeps sending the stuck slot to the classifier
    monkeypatch.setattr(graph_mod, "decide_next", lambda s: "classifier"
                        if s.get("classification") is None else "router")
    decision = await graph_mod.run_claim(tg, CLAIM, run_id="run-looptest", thread_id="t-loop-3")
    assert decision.degraded is True
    assert decision.routing is None


async def test_non_retryable_model_error_fails_fast_instead_of_cascading_retries():
    """Regression for docs/failure-analysis.md F-02: a 404 'model not found' is permanent — retrying
    it only multiplies latency (3 attempts + backoff per LLM call, on every node)."""
    from src.resilience import with_resilience

    attempts = {"n": 0}

    async def retired_model():
        attempts["n"] += 1
        raise RuntimeError("Error calling model 'gemini-x' (NOT_FOUND): 404 NOT_FOUND")

    result = await with_resilience(retired_model, component="probe", max_retries=2)
    assert attempts["n"] == 1
    assert result.error.kind == "unavailable"


async def test_rate_limit_waits_for_the_providers_retry_delay(monkeypatch):
    """Regression for docs/failure-analysis.md F-03: on a 429 the provider says how long to wait
    ('Please retry in 23.3s'). Retrying after a 1–2 s backoff burns every attempt inside the same
    quota window and degrades the claim."""
    import src.resilience as res

    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(res.asyncio, "sleep", fake_sleep)
    attempts = {"n": 0}

    async def rate_limited_once():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
                               "generate_content_free_tier_requests, limit: 5\nPlease retry in "
                               "23.33843568s. 'retryDelay': '23s'")
        return "ok"

    assert await res.with_resilience(rate_limited_once, component="probe", max_retries=2) == "ok"
    assert slept and slept[0] >= 23.0


async def test_transient_model_error_is_still_retried():
    from src.resilience import with_resilience

    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand")
        return "ok"

    assert await with_resilience(flaky, component="probe", max_retries=2) == "ok"
    assert attempts["n"] == 2
