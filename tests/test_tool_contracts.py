"""Tool-contract test — AC-12 (§7.6), SPEC-11 §2.5.

For **every** tool in `registry.list_tools()`: a valid input produces output matching the declared
schema, and **one error path** yields a typed error or a typed "not found" — never a crash that takes
the run down. The registry is enumerated, and `CONTRACTS` must cover it exactly, so a tool cannot be
added without a contract test (the test fails with the new tool's name).

Runs the real MCP server over stdio (no network, no API key) and the real RAG index.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import settings  # noqa: E402
from src.mcp_client import MCPToolset, _as_dict  # noqa: E402
from src.models import RetrievedClause  # noqa: E402
from src.tools import rag_tool, registry  # noqa: E402

POLICIES = json.loads((settings.policy_corpus_dir / "policies.json").read_text(encoding="utf-8"))
_first = POLICIES[0] if isinstance(POLICIES, list) else next(iter(POLICIES.values()))
POLICY = _first["policy_number"]
HISTORY = json.loads((settings.policy_corpus_dir / "claim_history.json").read_text(encoding="utf-8"))
CLAIMANT = next(iter(HISTORY)) if isinstance(HISTORY, dict) else HISTORY[0]["claimant_id"]

POLICY_KEYS = {"found", "policy_number", "product", "status", "inception_date", "expiry_date",
               "sum_insured", "deductible", "coverages", "exclusions", "reporting_deadline_days"}
HISTORY_KEYS = {"found", "claim_count", "claims"}
WINDOW_KEYS = {"found", "product", "policy_active_on_loss_date", "within_reporting_deadline",
               "days_to_report", "deadline_days", "days_since_inception_at_loss"}

# tool name → (valid kwargs, output check, error-path kwargs, error-path check)
CONTRACTS: dict[str, dict[str, Any]] = {
    "lookup_policy": {
        "valid": {"policy_number": POLICY},
        "keys": POLICY_KEYS,
        "error": {"policy_number": "POL-XX-0000000"},
        "error_expect": {"found": False},
    },
    "check_claim_history": {
        "valid": {"claimant_id": CLAIMANT, "months": 24},
        "keys": HISTORY_KEYS,
        "error": {"claimant_id": "CLT-000000", "months": 24},
        "error_expect": {"found": False, "claim_count": 0, "claims": []},
    },
    "validate_coverage_window": {
        "valid": {"policy_number": POLICY, "loss_date": "2026-09-01",
                  "reported_at": "2026-09-02T09:00:00Z"},
        "keys": WINDOW_KEYS,
        "error": {"policy_number": POLICY, "loss_date": "not-a-date",
                  "reported_at": "2026-09-02T09:00:00Z"},
        "error_expect": {"found": True, "error": "unparseable_date"},
    },
    "retrieve_policy_clauses": {
        "valid": {"query": "windscreen cracked by a stone", "product": "AUTO-COMP-2026", "k": 3},
        "error": {"query": "windscreen", "product": "NO-SUCH-PRODUCT", "k": 3},
    },
}


@pytest.fixture
async def mcp():
    async with MCPToolset() as toolset:
        assert toolset.available, f"MCP server did not start: {toolset.error}"
        yield toolset


@pytest.fixture(scope="module", autouse=True)
def _index():
    rag_tool.build_index()


async def test_every_registered_tool_has_a_contract(mcp):
    """Registry enumeration: adding a tool without a contract fails here, by name."""
    assert set(registry.list_tools()) == set(CONTRACTS), (
        f"uncovered: {set(registry.list_tools()) - set(CONTRACTS)}; "
        f"stale: {set(CONTRACTS) - set(registry.list_tools())}"
    )


async def test_every_tool_declares_a_description_and_input_schema(mcp):
    for spec in registry.all_specs():
        assert spec.description, spec.name
        assert spec.input_schema, f"{spec.name} has no declared input schema"


# ───────────────────────────────── MCP tools ──────────────────────────────────

MCP_TOOLS = ["lookup_policy", "check_claim_history", "validate_coverage_window"]


@pytest.mark.parametrize("name", MCP_TOOLS)
async def test_mcp_tool_valid_input_matches_schema(mcp, name):
    c = CONTRACTS[name]
    out = await mcp.call(name, **c["valid"])
    assert isinstance(out, dict) and out.get("found") is True
    assert c["keys"] <= set(out), f"{name} missing {c['keys'] - set(out)}"


@pytest.mark.parametrize("name", MCP_TOOLS)
async def test_mcp_tool_error_path_is_typed_not_a_crash(mcp, name):
    c = CONTRACTS[name]
    out = await mcp.call(name, **c["error"])
    for k, v in c["error_expect"].items():
        assert out.get(k) == v, f"{name}: {k}={out.get(k)!r}, expected {v!r}"


def test_mcp_responses_never_carry_a_plaintext_identifier():
    from src.security.masking import contains_unmasked_identifier

    lookups = [ln for ln in (settings.logs_dir / "mcp_transcript.jsonl")
               .read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lookups and not any(contains_unmasked_identifier(ln) for ln in lookups)


async def test_mcp_malformed_args_raise_a_typed_tool_error(mcp):
    """Missing a required argument is rejected by the tool's schema, not silently defaulted."""
    with pytest.raises(Exception) as exc_info:
        await mcp.call("lookup_policy")
    # the adapter's typed tool-execution error, carrying the server-side schema validation failure
    assert type(exc_info.value) is not Exception
    assert "validation error" in str(exc_info.value) and "policy_number" in str(exc_info.value)


async def test_mcp_server_down_degrades_to_empty_not_a_crash():
    from src.graph import TriageGraph

    class Down:
        available = False

    tg = TriageGraph(mcp=Down())  # type: ignore[arg-type]
    assert await tg._mcp("lookup_policy", policy_number=POLICY) == {}


# ────────────────────────────────── RAG tool ──────────────────────────────────


async def test_rag_valid_input_returns_citable_clauses():
    c = CONTRACTS["retrieve_policy_clauses"]
    out = await rag_tool.retrieve_policy_clauses(**c["valid"])
    assert 0 < len(out) <= c["valid"]["k"]
    for clause in out:
        RetrievedClause.model_validate(clause.model_dump())
        assert clause.product == "AUTO-COMP-2026"
        assert clause.clause_id.startswith("AUTO-COMP-2026 §")


async def test_rag_error_path_unknown_product_returns_empty_list():
    out = await rag_tool.retrieve_policy_clauses(**CONTRACTS["retrieve_policy_clauses"]["error"])
    assert out == []


# ───────────────────────── AC-07: every call is logged ────────────────────────


def test_tool_log_records_match_the_ac07_shape_and_registry_names():
    path = settings.logs_dir / "tool_calls.jsonl"
    records = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln]
    assert records
    required = {"timestamp", "agent", "tool_name", "args", "result", "latency_ms", "status"}
    for r in records[-20:]:
        assert required <= set(r)
        assert r["status"] in {"ok", "error", "timeout"}
        assert r["tool_name"] in CONTRACTS


def test_as_dict_unwraps_every_adapter_shape():
    payload = {"found": True, "product": "X"}
    s = json.dumps(payload)
    assert _as_dict(s) == payload
    assert _as_dict([{"type": "text", "text": s}]) == payload
    assert _as_dict(([{"type": "text", "text": s}], {"structured_content": {"result": s}})) == payload
