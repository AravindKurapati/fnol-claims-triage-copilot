"""Context engineering: write · select · compress · isolate — SPEC-03 §2.1.

Four named, called strategies — not four comments over one prompt string.

The one worth reading twice is `select_context` for the `router` node: it is handed *no free text at
all*. That is a control, not an optimisation. The routing decision is deterministic and must be
uninfluenceable by anything a claimant wrote, so the narrative never reaches it (design.md §D2).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from src.context.quarantine import QuarantinedText, render_for_prompt
from src.security.masking import mask_record, mask_text

# Per-node field allowlist (SPEC-03 §2.1). A worker sees a scoped sub-state, never the whole graph.
NODE_ALLOWLIST: dict[str, set[str]] = {
    "supervisor":  {"claim_facts", "untrusted", "memory_recall", "intent"},
    "classifier":  {"claim_facts", "untrusted", "memory_recall"},
    # The coverage agent sees the fenced narrative: exclusions turn on *how* a loss happened
    # (a track day, an unlicensed driver), which only the claimant's account records
    # (docs/failure-analysis.md F-04). It still reaches the prompt only via render_for_prompt().
    "coverage":    {"claim_facts", "classification", "retrieved", "policy", "coverage_rules",
                    "untrusted"},
    "fraud":       {"claim_facts", "classification", "untrusted", "claim_history", "policy"},
    "router":      {"claim_facts", "classification", "coverage", "fraud", "degraded"},
}

# Fields of claim_facts that `router` may see — amount and severity drivers only.
ROUTER_FACT_KEYS = {"claim_id", "estimated_amount", "currency", "injuries_reported"}


class ContextBundle(BaseModel):
    """What one node is allowed to see, already masked and already fenced."""

    node: str
    facts: dict[str, Any] = {}
    fenced_untrusted: str | None = None
    memory: list[str] = []
    retrieved: list[dict] = []
    extras: dict[str, Any] = {}

    def to_prompt(self) -> str:
        """Render the bundle as the user half of a prompt. Deterministic section order."""
        parts: list[str] = []
        if self.facts:
            parts.append("## Claim facts (trusted, system-validated)\n" + _kv(self.facts))
        if self.extras:
            for title, body in self.extras.items():
                parts.append(f"## {title}\n{_render(body)}")
        if self.retrieved:
            parts.append(
                "## Retrieved policy clauses (trusted)\n"
                + "\n\n".join(
                    f"[{c['clause_id']}] {c['clause_title']} ({c.get('kind','coverage')})\n{c['text']}"
                    for c in self.retrieved
                )
            )
        if self.memory:
            parts.append(
                "## Recalled from prior sessions (trusted, first-party)\n"
                + "\n".join(f"- {m}" for m in self.memory)
            )
        if self.fenced_untrusted:
            parts.append("## Claimant narrative\n" + self.fenced_untrusted)
        return "\n\n".join(parts)


def _kv(d: dict[str, Any]) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in d.items())


def _render(value: Any) -> str:
    if isinstance(value, dict):
        return _kv(value)
    if isinstance(value, list):
        return "\n".join(f"- {v}" for v in value)
    return str(value)


# ───────────────────────────────── write ──────────────────────────────────────


def write_context(state: dict, scope: str, key: str, value: Any) -> dict:
    """WRITE — persist a fact *outside* the prompt.

    scope="run" → the run scratchpad (cheap, disposable)
    scope="claimant" → long-term memory (SPEC-04); the caller awaits `memory.remember`
    """
    pad = dict(state.get("scratchpad") or {})
    pad.setdefault(scope, {})
    pad[scope] = {**pad[scope], key: value}
    return pad


def read_context(state: dict, scope: str, key: str, default: Any = None) -> Any:
    return (state.get("scratchpad") or {}).get(scope, {}).get(key, default)


# ───────────────────────────────── select ─────────────────────────────────────


def select_context(
    state: dict,
    node: str,
    *,
    extras: dict[str, Any] | None = None,
    retrieved_k: int = 6,
    memory_k: int = 5,
) -> ContextBundle:
    """SELECT — assemble only what `node` is allowed to see, masked and fenced."""
    allow = NODE_ALLOWLIST.get(node, set())
    claim = state.get("claim")

    facts: dict[str, Any] = {}
    if "claim_facts" in allow and claim is not None:
        facts = claim.trusted_facts()
        if node == "router":
            facts = {k: v for k, v in facts.items() if k in ROUTER_FACT_KEYS}
        facts = mask_record(facts)

    fenced: str | None = None
    if "untrusted" in allow:
        qt = state.get("untrusted")
        if isinstance(qt, QuarantinedText):
            fenced = render_for_prompt(qt)  # the only sanctioned path (SPEC-03 §2.2)

    memory: list[str] = []
    if "memory_recall" in allow:
        memory = [mask_text(m) for m in (state.get("memory_recall") or [])][:memory_k]

    retrieved: list[dict] = []
    if "retrieved" in allow:
        seen: set[str] = set()
        for c in state.get("retrieved") or []:
            cid = c.clause_id if hasattr(c, "clause_id") else c["clause_id"]
            if cid in seen:
                continue
            seen.add(cid)
            retrieved.append(c.model_dump() if hasattr(c, "model_dump") else dict(c))
        retrieved = retrieved[:retrieved_k]

    scoped_extras: dict[str, Any] = {}
    for title, body in (extras or {}).items():
        scoped_extras[title] = mask_record(body)

    for name in ("classification", "coverage", "fraud", "intent"):
        if name in allow and state.get(name) is not None:
            obj = state[name]
            scoped_extras[name.capitalize()] = (
                obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
            )
    if "degraded" in allow:
        scoped_extras["Run health"] = {"degraded": bool(state.get("degraded"))}

    return ContextBundle(
        node=node,
        facts=facts,
        fenced_untrusted=fenced,
        memory=memory,
        retrieved=retrieved,
        extras=scoped_extras,
    )


# ──────────────────────────────── isolate ─────────────────────────────────────


def isolate(state: dict, node: str) -> dict:
    """ISOLATE (form b) — a scoped sub-state dict for a worker.

    Form (a), isolating untrusted text into a non-instruction channel, is `QuarantinedText` itself.
    """
    allow = NODE_ALLOWLIST.get(node, set())
    keys = {
        "claim_facts": "claim",
        "untrusted": "untrusted",
        "memory_recall": "memory_recall",
        "retrieved": "retrieved",
        "classification": "classification",
        "coverage": "coverage",
        "fraud": "fraud",
        "intent": "intent",
        "degraded": "degraded",
    }
    return {dst: state.get(dst) for src, dst in keys.items() if src in allow}


def context_contains_free_text(bundle: ContextBundle) -> bool:
    """Assertion helper — SPEC-03 §5 checks the router's bundle is free-text-free."""
    return bundle.fenced_untrusted is not None
