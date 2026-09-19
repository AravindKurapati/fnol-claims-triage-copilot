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
