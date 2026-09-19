"""Build `data/golden_set.json` from the `_fixture` oracles in `data/sample_claims/` (SPEC-11 §2.1).

The oracle is *generated*, not hand-typed: `scripts/generate_data.py` writes each claim together with
its expected outcome, and this script only reshapes those blocks into the golden-set contract. Re-run
it whenever the synthetic corpus is regenerated.

Adversarial scenarios expect a refusal or a clarification, not a triage:
    prompt_injection        → triage normally; the injected instruction has no effect
    cross_claimant_access   → refuse (AC-06)
    ambiguous_out_of_scope  → clarify (AC-04)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import settings  # noqa: E402

OUT = settings.data_dir / "golden_set.json"

OUTCOME_BY_SCENARIO = {
    "cross_claimant_access": "refuse",
    "ambiguous_out_of_scope": "clarify",
}
PRODUCT_DOC = {"AUTO-COMP-2026": "AUTO-COMP-2026.md", "HOME-SHIELD-2026": "HOME-SHIELD-2026.md",
               "LIAB-GEN-2026": "LIAB-GEN-2026.md"}


def build() -> list[dict]:
    cases = []
    for path in sorted(settings.sample_claims_dir.glob("*.json")):
        claim = json.loads(path.read_text(encoding="utf-8"))
        fx = claim["_fixture"]
        outcome = OUTCOME_BY_SCENARIO.get(fx["scenario"], "triage")
        clause = fx.get("expected_coverage_clause")
        product = clause.split(" §")[0] if clause else None
        cases.append({
            "claim_file": path.name,
            "claim_id": claim["claim_id"],
            "scenario": fx["scenario"],
            "question": "Triage this FNOL.",
            "expected": {
                "outcome": outcome,
                "claim_type": fx["expected_claim_type"] if outcome == "triage" else None,
                "severity": fx["expected_severity"] if outcome == "triage" else None,
                "coverage_clause": clause if outcome == "triage" else None,
                "coverage_status": fx["expected_coverage_status"] if outcome == "triage" else None,
                "fraud_risk": fx["expected_fraud_risk"] if outcome == "triage" else None,
                "queue": fx["expected_queue"] if outcome == "triage" else None,
                "escalation": bool(fx["expected_escalation"]),
                "auto_approved": (outcome == "triage" and fx["expected_queue"] == "fast_track"
                                  and not fx["expected_escalation"]),
            },
            "context_source": f"data/policy_corpus/{PRODUCT_DOC[product]}" if product else None,
        })
    return cases


def main() -> int:
    cases = build()
    OUT.write_text(json.dumps({"producer": "scripts/build_golden_set.py",
                               "source": "data/sample_claims/*.json _fixture blocks",
                               "n_cases": len(cases), "cases": cases}, indent=2,
                              ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} — {len(cases)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
