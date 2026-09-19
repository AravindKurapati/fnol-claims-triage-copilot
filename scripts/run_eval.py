"""Agent-level evaluation — AC-12, SPEC-11 §2.2 → `reports/eval_report.json`.

    python scripts/run_eval.py                  # score the latest full traced run in runs/
    python scripts/run_eval.py --run-id run-…   # score a specific run
    python scripts/run_eval.py --no-judge       # deterministic metrics only (no model calls)

Scores the decisions the copilot actually produced (`runs/<run_id>_<claim_id>.json`, written by
`python -m src.main trace` / `batch`) against the generated oracle in `data/golden_set.json`
(`scripts/build_golden_set.py`). The graph is not re-run here: the evaluated decisions are the same
ones whose spans are in `traces/phoenix_spans.parquet`, so every score reconciles with a trace.

Deterministic metrics (no model):
    routing_accuracy    queue matches on triage cases; the outcome (clarify / refuse) matches on
                        adversarial cases. Also reported as the headline `accuracy`.
    escalation_recall   of the triage cases the oracle escalates, the share the copilot escalated.
                        Must be 1.0: a missed escalation is the worst failure (AC-03).
    no_auto_approval_violations  escalated decisions that were also auto-approved. Must be 0.
    citation_validity   cited clauses that exist in the policy corpus.
    clause_accuracy, coverage_status_accuracy, claim_type_accuracy, severity_accuracy,
    fraud_risk_accuracy on triage cases; adversarial_handling on the three adversarial scenarios.

LLM-as-judge metrics (DeepEval; judge = Gemini `GEMINI_JUDGE_MODEL`, Rule R4), on every triage
case that produced a coverage assessment:
    HallucinationMetric   coverage rationale contradicted by the retrieved clause text.
                          deepeval >= 4 scores it like every other metric (1 = no contradiction),
                          so `hallucination_rate` = 1 - mean score (lower is better)
    FaithfulnessMetric    coverage rationale grounded in the retrieved clauses
    AnswerRelevancyMetric the decision summary addresses the FNOL that was filed

The judge goes through `src/llm.py` (the project's single Gemini client) with its own pacing and
the provider's 429 retry delay honoured, because the free tier allows only a few requests a minute.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("DEEPEVAL_GRPC_LOGGING", "NO")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import settings  # noqa: E402
from src.security.masking import mask_record, mask_text  # noqa: E402

GOLDEN = settings.data_dir / "golden_set.json"
OUT = ROOT / "reports/eval_report.json"
JUDGE_THRESHOLD = 0.5


# ─────────────────────────────── judge model ──────────────────────────────────


def build_judge() -> Any:
    """A DeepEval judge backed by the project's Gemini client (never another provider)."""
    from deepeval.models import DeepEvalBaseLLM
    from langchain_core.messages import HumanMessage

    from src.llm import get_llm
    from src.resilience import is_permanent, retry_after

    class GeminiJudge(DeepEvalBaseLLM):
        def __init__(self, model: str) -> None:
            self.model_id = model
            self.calls = 0
            self._next = 0.0
            super().__init__(model)

        def load_model(self) -> Any:
            return get_llm(model=self.model_id, temperature=0.0)

        def _pace(self) -> None:
            interval = 60.0 / max(settings.gemini_rpm, 1)
            now = time.monotonic()
            if self._next > now:
                time.sleep(self._next - now)
            self._next = max(now, self._next) + interval

        def generate(self, prompt: str, schema: Any = None) -> Any:
            runnable = self.model.with_structured_output(schema) if schema else self.model
            for attempt in range(settings.max_retries + 3):
                self._pace()
                try:
                    self.calls += 1
                    out = runnable.invoke([HumanMessage(content=prompt)])
                    if schema is not None:
                        return out if isinstance(out, schema) else schema.model_validate(out)
                    return out.content if isinstance(out.content, str) else str(out.content)
                except Exception as exc:  # noqa: BLE001
                    if is_permanent(exc) or attempt == settings.max_retries + 2:
                        raise
                    time.sleep(retry_after(str(exc)) or 2.0 * (attempt + 1))
            raise RuntimeError("unreachable")

        async def a_generate(self, prompt: str, schema: Any = None) -> Any:
            return self.generate(prompt, schema=schema)

        def get_model_name(self) -> str:
            return f"gemini/{self.model_id}"

    return GeminiJudge(settings.gemini_judge_model)


# ─────────────────────────────── loading ──────────────────────────────────────


def latest_full_run(case_ids: set[str]) -> str:
    """The most recent run in runs/ that decided every golden case."""
    by_run: dict[str, set[str]] = {}
    mtime: dict[str, float] = {}
    for f in settings.runs_dir.glob("run-*_CLM-*.json"):
        run_id, claim_id = f.stem.split("_", 1)
        by_run.setdefault(run_id, set()).add(claim_id)
        mtime[run_id] = max(mtime.get(run_id, 0.0), f.stat().st_mtime)
    full = [r for r, ids in by_run.items() if case_ids <= ids]
    if not full:
        raise SystemExit("no run in runs/ covers the golden set — run `python -m src.main trace` "
                         "(or `batch`) first")
    return max(full, key=mtime.__getitem__)


def load_decision(run_id: str, claim_id: str) -> dict[str, Any]:
    return json.loads((settings.runs_dir / f"{run_id}_{claim_id}.json").read_text(encoding="utf-8"))


def outcome_of(d: dict[str, Any]) -> str:
    if d.get("degraded"):
        return "degraded"
    if d.get("routing"):
        return "triage"
    kind = (d.get("intent") or {}).get("kind")
    return {"other_claimant_data": "refuse", "out_of_scope": "clarify",
            "ambiguous": "clarify", "claim_status": "clarify"}.get(kind, "unknown")


# ─────────────────────────────── scoring ──────────────────────────────────────


def ratio(hits: int, n: int) -> float | None:
    return round(hits / n, 4) if n else None


def deterministic(case: dict[str, Any], d: dict[str, Any], corpus_ids: set[str]) -> dict[str, Any]:
    exp = case["expected"]
    got_outcome = outcome_of(d)
    routing = d.get("routing") or {}
    cls = d.get("classification") or {}
    cov = d.get("coverage") or {}
    fraud = d.get("fraud") or {}
    cited = cov.get("cited_clause")
    m: dict[str, Any] = {"outcome": got_outcome, "outcome_ok": got_outcome == exp["outcome"]}
    if exp["outcome"] == "triage":
        m.update({
            "queue": routing.get("queue"), "queue_ok": routing.get("queue") == exp["queue"],
            "escalated": bool(routing.get("escalation_required")),
            "escalation_ok": bool(routing.get("escalation_required")) == exp["escalation"],
            "claim_type_ok": cls.get("claim_type") == exp["claim_type"],
            "severity_ok": cls.get("severity") == exp["severity"],
            "cited_clause": cited, "clause_ok": cited == exp["coverage_clause"],
            "coverage_status_ok": cov.get("status") == exp["coverage_status"],
            "fraud_risk_ok": fraud.get("risk") == exp["fraud_risk"],
        })
    m["citation_valid"] = None if not cited else cited in corpus_ids
    m["auto_approval_violation"] = bool(routing.get("escalation_required")
                                        and routing.get("auto_approved"))
    return m


def judge_case(judge: Any, claim: dict[str, Any], d: dict[str, Any]) -> dict[str, Any] | None:
    """DeepEval metrics on one decision. None when there is no coverage reading to judge."""
    from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, HallucinationMetric
    from deepeval.test_case import LLMTestCase

    cov = d.get("coverage") or {}
    if not cov.get("rationale") or not d.get("routing"):
        return None
    seen: set[str] = set()
    context = []
    for c in d.get("retrieved") or []:
        if c["clause_id"] not in seen:
            seen.add(c["clause_id"])
            context.append(f"{c['clause_id']} {c['clause_title']}: {c['text']}")
    if cov.get("clause_text") and cov.get("cited_clause") not in seen:
        context.append(f"{cov['cited_clause']}: {cov['clause_text']}")
    if not context:
        return None

    cls, routing = d.get("classification") or {}, d["routing"]
    answer = mask_text(
        f"Coverage: {cov['status']} under {cov.get('cited_clause') or 'no clause'}. "
        f"{cov['rationale']} "
        f"Classification: {cls.get('claim_type')}, {cls.get('severity')}. "
        f"Route: {routing['queue']} — {routing['rationale']}"
        + (f" Escalated: {routing['escalation_reason']}." if routing.get("escalation_required")
           else "")
    )
    question = mask_text(f"Triage this first notice of loss ({claim['line_of_business']}, "
                         f"{claim['currency']} {claim['estimated_amount']:,}): "
                         f"{claim['description']}")
    tc = LLMTestCase(input=question, actual_output=answer, context=context,
                     retrieval_context=context)

    out: dict[str, Any] = {}
    for key, metric in (
        ("no_hallucination", HallucinationMetric(threshold=JUDGE_THRESHOLD, model=judge,
                                              async_mode=False)),
        ("faithfulness", FaithfulnessMetric(threshold=JUDGE_THRESHOLD, model=judge,
                                            async_mode=False)),
        ("answer_relevancy", AnswerRelevancyMetric(threshold=JUDGE_THRESHOLD, model=judge,
                                                   async_mode=False)),
    ):
        try:
            metric.measure(tc)
            out[key] = {"score": round(float(metric.score), 4), "passed": metric.is_successful(),
                        "reason": mask_text(metric.reason or "")[:500]}
        except Exception as exc:  # noqa: BLE001 - one failed judgement must not sink the report
            out[key] = {"score": None, "passed": None,
                        "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    return out


def _complement(score: float | None) -> float | None:
    return None if score is None else round(1.0 - score, 4)


def mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


# ─────────────────────────────── main ─────────────────────────────────────────


def evaluate(run_id: str | None, use_judge: bool) -> dict[str, Any]:
    from src.tools.rag_tool import load_all_clauses

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cases = golden["cases"]
    run_id = run_id or latest_full_run({c["claim_id"] for c in cases})
    corpus_ids = {c["clause_id"] for c in load_all_clauses()}
    judge = build_judge() if use_judge else None

    rows = []
    for case in cases:
        d = load_decision(run_id, case["claim_id"])
        claim = json.loads((settings.sample_claims_dir / case["claim_file"]).read_text(
            encoding="utf-8"))
        det = deterministic(case, d, corpus_ids)
        judged = judge_case(judge, claim, d) if judge else None
        passed = det["outcome_ok"] and det.get("queue_ok", True) and det.get(
            "escalation_ok", True) and not det["auto_approval_violation"]
        rows.append({"claim_id": case["claim_id"], "scenario": case["scenario"],
                     "expected": case["expected"], "passed": passed, "deterministic": det,
                     "judge": judged})
        scores = ", ".join(f"{k}={v.get('score')}" for k, v in (judged or {}).items())
        print(f"  {case['claim_id']} {case['scenario']:<24} {'PASS' if passed else 'FAIL'}"
              + (f"  judge: {scores}" if scores else ""))

    triage = [r for r in rows if r["expected"]["outcome"] == "triage"]
    adversarial = [r for r in rows if r["scenario"] in
                   {"prompt_injection", "cross_claimant_access", "ambiguous_out_of_scope"}]
    must_escalate = [r for r in triage if r["expected"]["escalation"]]
    cited = [r for r in rows if r["deterministic"]["citation_valid"] is not None]

    def acc(rs: list[dict], key: str) -> float | None:
        return ratio(sum(bool(r["deterministic"].get(key)) for r in rs), len(rs))

    routing_hits = sum((r["deterministic"]["queue_ok"] if r["expected"]["outcome"] == "triage"
                        else r["deterministic"]["outcome_ok"]) for r in rows)
    judged = [r["judge"] for r in rows if r["judge"]]

    metrics = {
        "routing_accuracy": ratio(routing_hits, len(rows)),
        "escalation_recall": ratio(sum(r["deterministic"]["escalated"] for r in must_escalate),
                                   len(must_escalate)),
        "no_auto_approval_violations": sum(r["deterministic"]["auto_approval_violation"]
                                           for r in rows),
        "citation_validity": ratio(sum(r["deterministic"]["citation_valid"] for r in cited),
                                   len(cited)),
        "clause_accuracy": acc(triage, "clause_ok"),
        "coverage_status_accuracy": acc(triage, "coverage_status_ok"),
        "claim_type_accuracy": acc(triage, "claim_type_ok"),
        "severity_accuracy": acc(triage, "severity_ok"),
        "fraud_risk_accuracy": acc(triage, "fraud_risk_ok"),
        "adversarial_handling": acc(adversarial, "outcome_ok"),
        "hallucination_rate": _complement(mean([j["no_hallucination"]["score"] for j in judged])),
        "faithfulness": mean([j["faithfulness"]["score"] for j in judged]),
        "answer_relevancy": mean([j["answer_relevancy"]["score"] for j in judged]),
    }
    return mask_record({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "producer": "scripts/run_eval.py",
        "golden_set": "data/golden_set.json",
        "run_id": run_id,
        "decisions": f"runs/{run_id}_<claim_id>.json (spans in traces/phoenix_spans.parquet)",
        "model": settings.gemini_model,
        "judge": settings.gemini_judge_model if judge else None,
        "judge_framework": "deepeval" if judge else None,
        "judge_calls": getattr(judge, "calls", 0),
        "judge_threshold": JUDGE_THRESHOLD,
        "n_cases": len(rows),
        "n_judged": len(judged),
        "metrics": metrics,
        "accuracy": metrics["routing_accuracy"],
        "hallucination_rate": metrics["hallucination_rate"],
        "passed": sum(r["passed"] for r in rows),
        "failures": dict(Counter(r["scenario"] for r in rows if not r["passed"])),
        "cases": rows,
    })


def main() -> int:
    ap = argparse.ArgumentParser(description="DeepEval + deterministic agent evaluation (AC-12)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--no-judge", action="store_true", help="skip the Gemini judge metrics")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    report = evaluate(args.run_id, use_judge=not args.no_judge)
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    m = report["metrics"]
    print(f"wrote {args.out} — run {report['run_id']}, "
          f"{report['passed']}/{report['n_cases']} cases pass; "
          f"routing {m['routing_accuracy']}, escalation recall {m['escalation_recall']}, "
          f"citation validity {m['citation_validity']}, hallucination {m['hallucination_rate']}")
    ok = m["escalation_recall"] in (1.0, None) and m["no_auto_approval_violations"] == 0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
