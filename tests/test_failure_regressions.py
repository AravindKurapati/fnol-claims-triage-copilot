"""Regression tests for the failures documented in `docs/failure-analysis.md` (AC-08).

Each failure was observed in a real traced run; each test below reproduced it before the fix and
guards it after. F-01, F-02 and F-03 live next to the component they exercise
(`tests/test_memory_persistence.py`, `tests/test_routing.py`, `tests/test_loops.py`); the ones here
are about what reaches the model. Deterministic — no API key.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json  # noqa: E402

from src.agents.classifier import CLAIM_TYPES, normalise_claim_type  # noqa: E402
from src.config import settings  # noqa: E402
from src.context.quarantine import OPEN_FENCE, quarantine  # noqa: E402
from src.context.strategies import select_context  # noqa: E402
from src.models import ClaimRecord, Classification  # noqa: E402

RAW = json.loads((settings.sample_claims_dir / "claim_008.json").read_text(encoding="utf-8"))
RAW.pop("_fixture", None)
CLAIM = ClaimRecord.model_validate(RAW)


def _state():
    return {
        "claim": CLAIM,
        "untrusted": quarantine(CLAIM.description, source="claimant_description",
                                claim_id=CLAIM.claim_id),
        "classification": Classification(claim_type="collision", severity="major",
                                          confidence=0.9, evidence=["hit the barrier"]),
        "retrieved": [],
    }


# ── F-04 — the coverage agent never saw the circumstances that trigger an exclusion ──


def test_coverage_context_carries_the_fenced_narrative():
    """claim_008 is a track-day crash: the racing exclusion (§6.1) can only be applied by an agent
    that sees *how* the loss happened, and that is only in the narrative."""
    prompt = select_context(_state(), "coverage").to_prompt()
    assert OPEN_FENCE in prompt, "narrative must reach coverage — and only inside the fence"
    assert "track day" in prompt


def test_router_still_never_sees_the_narrative():
    """The fix must not widen the router's view: routing stays uninfluenceable by claimant text."""
    prompt = select_context(_state(), "router").to_prompt()
    assert OPEN_FENCE not in prompt and "track day" not in prompt


# ── F-06 — trace spans carried plaintext policy numbers and claimant ids ──


def test_span_exporter_masks_identifiers_before_they_leave_the_process():
    """The LangChain instrumentor serialises the whole graph state (raw_input, claim) into span
    input/output values. Every exported span must be masked first (NFR-05)."""
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from src.observability.tracing import MaskingSpanExporter
    from src.security.masking import contains_unmasked_identifier

    inner = InMemorySpanExporter()
    span = ReadableSpan(
        name="LangGraph",
        attributes={
            "input.value": json.dumps({"raw_input": {"policy_number": "POL-AU-4471209",
                                                     "claimant": {"claimant_id": "CLT-882134"}}}),
            "llm.input_messages.0.message.content": "claimant CLT-882134 on POL-AU-4471209",
            "fnol.claim_id": "CLM-2026-000001",
        },
    )
    MaskingSpanExporter(inner).export([span])
    (out,) = inner.get_finished_spans()
    assert not any(contains_unmasked_identifier(str(v)) for v in out.attributes.values())
    assert "POL-AU-****209" in out.attributes["input.value"]
    assert out.attributes["fnol.claim_id"] == "CLM-2026-000001"  # claim ids are not personal ids


def test_trace_export_masks_spans_recorded_before_the_fix():
    """Defence in depth: spans already in Phoenix from pre-fix runs are masked on export."""
    import pandas as pd

    sys.path.insert(0, str(ROOT / "scripts"))
    from export_traces import _normalise

    df = pd.DataFrame({
        "context.span_id": ["a" * 16], "context.trace_id": ["b" * 32], "name": ["ingest"],
        "start_time": ["2026-09-19T00:00:00Z"], "end_time": ["2026-09-19T00:00:01Z"],
        "attributes.input.value": ['{"policy_number": "POL-HM-3390562"}'],
    })
    out = _normalise(df)
    assert out["attributes.input.value"].iloc[0] == '{"policy_number": "POL-HM-****562"}'


def test_trace_export_never_masks_the_span_ids_it_is_cited_by():
    """A hex span id can contain a 10-digit run ("c6ed6802835677a5") that the phone regex matches.
    Masking it would break every citation of that span (Rule R2)."""
    import pandas as pd

    sys.path.insert(0, str(ROOT / "scripts"))
    from export_traces import _normalise

    df = pd.DataFrame({
        "context.span_id": ["c6ed6802835677a5"], "context.trace_id": ["5cff3ce1d7b4a83132bcf464340259d6"],
        "parent_id": ["fb77ec54955c0cac"], "name": ["LangGraph"],
        "start_time": ["2026-09-19T00:00:00Z"], "end_time": ["2026-09-19T00:00:01Z"],
    })
    out = _normalise(df)
    assert out["context.span_id"].iloc[0] == "c6ed6802835677a5"
    assert out["context.trace_id"].iloc[0] == "5cff3ce1d7b4a83132bcf464340259d6"


# ── F-07 — OG-02 redacted the word "Queue" as a PERSON in every escalation instruction ──


async def test_output_pii_guard_leaves_system_templates_intact():
    from src.guardrails.output_guard import screen_output
    from src.models import RoutingDecision

    msg = ("Escalate to a human claims handler — high-value claim (820,000 >= 500,000). "
           "Queue: standard. This is a recommendation, not an approval.")
    routing = RoutingDecision(queue="standard", rationale="x", escalation_required=True,
                              escalation_reason="high-value claim")
    verdict = await screen_output({"terminal_message": msg, "routing": routing, "retrieved": []})
    assert "OG-02" not in verdict.rule_ids
    assert (verdict.sanitized or msg) == msg


async def test_output_pii_guard_scrubs_model_text_that_quotes_the_narrative():
    """The real leak surface: model-written fields quoting the claimant's narrative."""
    from src.guardrails.output_guard import screen_output

    cls = Classification(claim_type="collision", severity="minor", confidence=0.9,
                         evidence=["my neighbour Rahul Mehta reversed into the car",
                                   "call me on 98765 43210"])
    verdict = await screen_output({"terminal_message": "ok", "classification": cls,
                                   "retrieved": []})
    assert "OG-02" in verdict.rule_ids
    scrubbed = " ".join(verdict.field_updates["classification"].evidence)
    assert "Rahul Mehta" not in scrubbed and "98765 43210" not in scrubbed


# ── F-05 — classifier echoed the prompt's "auto: collision" layout as the label ──


def test_claim_type_labels_are_normalised_to_the_vocabulary():
    assert normalise_claim_type("auto: collision", "auto") == "collision"
    assert normalise_claim_type("Property: Water Damage", "property") == "water_damage"
    assert normalise_claim_type("property - burglary", "property") == "burglary"
    assert normalise_claim_type("glass", "auto") == "glass"


def test_unrecognised_claim_type_becomes_unknown_not_free_text():
    assert normalise_claim_type("spaceship damage", "auto") == "unknown"
    assert normalise_claim_type("", "auto") == "unknown"


def test_claim_type_vocabulary_is_scoped_by_line_of_business():
    assert normalise_claim_type("burglary", "auto") == "unknown"  # not an auto loss type
    assert "fire" in CLAIM_TYPES["auto"] and "fire" in CLAIM_TYPES["property"]


# ── F-08 — tracing silently disabled: the traced run exported nothing, and `trace` said OK ──


def test_tracing_initialises_against_the_installed_phoenix_otel(monkeypatch):
    """phoenix-otel's TracerProvider no longer takes `project_name`; init must still enable
    tracing and tag spans with the project (no collector needed — export is batched)."""
    from src.observability import tracing

    monkeypatch.setattr(tracing, "_PROVIDER", None)
    monkeypatch.setattr(tracing.settings, "phoenix_enabled", True)
    monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", lambda p: None)
    monkeypatch.setattr(
        "openinference.instrumentation.langchain.LangChainInstrumentor.instrument",
        lambda self, **kw: None)
    tracing.init_tracing(project_name="fnol-regression")
    try:
        assert tracing.tracing_enabled(), "init_tracing fell back to untraced"
        resource = tracing._PROVIDER.resource.attributes
        assert "fnol-regression" in resource.values()
    finally:
        tracing._PROVIDER.shutdown()


def test_trace_command_fails_instead_of_exporting_an_untraced_run(monkeypatch):
    """`trace` exists to produce spans; if tracing is off it must say so and exit non-zero."""
    import argparse
    import asyncio

    from src import main as cli

    sys.path.insert(0, str(ROOT / "scripts"))
    import export_traces

    monkeypatch.setattr(export_traces, "ensure_phoenix", lambda *a, **k: True)
    monkeypatch.setattr(export_traces, "register_model_prices", lambda: [])
    monkeypatch.setattr(cli, "init_tracing", lambda *a, **k: "run-000000000000")
    monkeypatch.setattr(cli, "tracing_enabled", lambda: False, raising=False)

    async def _no_batch(args):  # the batch must never start untraced
        raise AssertionError("batch ran without tracing")

    monkeypatch.setattr(cli, "cmd_batch", _no_batch)
    args = argparse.Namespace(dir=str(settings.sample_claims_dir), all=False)
    assert asyncio.run(cli.cmd_trace(args)) == cli.EXIT_USAGE
