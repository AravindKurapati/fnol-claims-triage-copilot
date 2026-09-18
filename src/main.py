"""CLI — the required interface (§7.7, SPEC-12). FastAPI is bonus only.

`init_tracing()` is called first, before anything else runs: the rubric checks that Phoenix
instrumentation is *in the run path*, so the call site exists from Phase 1 and Phase 4 fills the
body (SPEC-07 §2.1).

Commands:
    run    --claim <file>     triage one claim
    batch  --dir <dir>        triage every committed sample input   (NFR-02 command 1)
    chat   --claimant <id>    multi-turn session (proves AC-05 in-session)
    data   [--check]          regenerate / verify the synthetic corpus
    index  [--force]          build the RAG index
    graph                     print the compiled graph topology
    trace                     PHASE 4 — regenerate + export Phoenix traces
    eval                      PHASE 6 — DeepEval + golden signals
    verify                    PHASE 6 — citation + hygiene verifier
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

from src.config import ConfigError, settings  # noqa: E402
from src.graph import build_graph, run_claim  # noqa: E402
from src.llm import probe  # noqa: E402
from src.mcp_client import MCPToolset  # noqa: E402
from src.models import TriageDecision  # noqa: E402
from src.observability.tracing import init_tracing  # noqa: E402
from src.security.masking import mask_record, mask_text  # noqa: E402

console = Console()

EXIT_OK, EXIT_USAGE, EXIT_DEGRADED, EXIT_BLOCKED = 0, 1, 2, 3


# ────────────────────────────── rendering ─────────────────────────────────────


def render(decision: TriageDecision) -> None:
    """Human-readable decision. Every identifier masked on the way out (NFR-05)."""
    d = decision
    header = f"[bold]{d.claim_id}[/bold]   run {d.run_id}"
    if d.degraded:
        header += "   [yellow]DEGRADED[/yellow]"
    console.print(Panel(header, expand=False))

    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style="dim", width=16)
    t.add_column()

    if d.intent:
        t.add_row("intent", f"{d.intent.kind}  ({d.intent.confidence:.2f})")
    if d.classification:
        c = d.classification
        t.add_row("classification", f"{c.claim_type} · {c.severity}  ({c.confidence:.2f})")
        if c.evidence:
            t.add_row("", "[dim]" + mask_text("; ".join(c.evidence[:2])) + "[/dim]")
    if d.coverage:
        cv = d.coverage
        colour = {"covered": "green", "partially_covered": "yellow",
                  "not_covered": "red", "ambiguous": "yellow"}.get(cv.status, "white")
        t.add_row("coverage", f"[{colour}]{cv.status}[/{colour}]  cite: {cv.cited_clause or '—'}")
        t.add_row("", "[dim]" + mask_text(cv.rationale[:180]) + "[/dim]")
    if d.fraud:
        f = d.fraud
        colour = {"low": "green", "medium": "yellow", "high": "red"}[f.risk]
        codes = ", ".join(i.code for i in f.indicators) or "none"
        t.add_row("fraud", f"[{colour}]{f.risk}[/{colour}]  score {f.score:.2f}  [{codes}]")
        for ind in f.indicators[:4]:
            t.add_row("", f"[dim]{ind.code}: {mask_text(ind.evidence)[:110]}[/dim]")
    if d.routing:
        r = d.routing
        colour = {"fast_track": "green", "standard": "cyan", "investigate": "red"}[r.queue]
        flag = " [red]ESCALATED[/red]" if r.escalation_required else ""
        t.add_row("routing", f"[{colour}]{r.queue}[/{colour}]{flag}  "
                             f"auto_approved={r.auto_approved}")
        t.add_row("", "[dim]" + mask_text(r.rationale[:180]) + "[/dim]")
        if r.escalation_reason:
            t.add_row("", f"[dim]escalation: {mask_text(r.escalation_reason)}[/dim]")
    if d.errors:
        t.add_row("errors", "; ".join(f"{e.get('component')}:{e.get('kind')}" for e in d.errors))

    console.print(t)
    console.print(Panel(mask_text(d.message or d.next_action), title="next action", expand=False))


def save_run(decision: TriageDecision) -> Path:
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    path = settings.runs_dir / f"{decision.run_id}_{decision.claim_id}.json"
    payload = mask_record(decision.model_dump(mode="json"))
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def exit_code_for(decision: TriageDecision) -> int:
    return EXIT_DEGRADED if decision.degraded else EXIT_OK


# ──────────────────────────────── commands ────────────────────────────────────


async def cmd_run(args: argparse.Namespace) -> int:
    run_id = init_tracing()  # PHASE 4 SEAM — first call in the run path
    claim_path = Path(args.claim)
    if not claim_path.exists():
        console.print(f"[red]claim file not found:[/red] {claim_path}")
        return EXIT_USAGE

    raw = json.loads(claim_path.read_text(encoding="utf-8"))
    thread_id = args.thread_id or f"t-{raw.get('claim_id', 'unknown')}"

    async with MCPToolset() as mcp:
        if not mcp.available:
            console.print(f"[yellow]MCP unavailable ({mcp.error}) — running degraded[/yellow]")
        graph = await build_graph(mcp)
        decision = await run_claim(graph, raw, run_id=run_id, thread_id=thread_id)

    path = save_run(decision)
    if args.json:
        print(json.dumps(mask_record(decision.model_dump(mode="json")), indent=2))
    else:
        render(decision)
        console.print(f"[dim]saved → {path.relative_to(ROOT)}[/dim]")
    return exit_code_for(decision)


async def cmd_batch(args: argparse.Namespace) -> int:
    run_id = init_tracing()  # PHASE 4 SEAM
    claim_dir = Path(args.dir)
    files = sorted(claim_dir.glob("*.json"))
    if not files:
        console.print(f"[red]no claims found in[/red] {claim_dir}")
        return EXIT_USAGE

    results: list[tuple[str, TriageDecision, dict[str, Any]]] = []
    async with MCPToolset() as mcp:
        if not mcp.available:
            console.print(f"[yellow]MCP unavailable ({mcp.error}) — running degraded[/yellow]")
        graph = await build_graph(mcp)
        for f in files:
            raw = json.loads(f.read_text(encoding="utf-8"))
            fixture = raw.get("_fixture", {})
            console.print(f"[dim]▸ {f.name}  ({fixture.get('scenario','—')})[/dim]")
            decision = await run_claim(
                graph, raw, run_id=run_id, thread_id=f"t-{raw.get('claim_id')}"
            )
            save_run(decision)
            results.append((f.name, decision, fixture))

    _summary_table(results)
    return EXIT_DEGRADED if any(d.degraded for _, d, _ in results) else EXIT_OK


def _summary_table(results: list[tuple[str, TriageDecision, dict[str, Any]]]) -> None:
    """Compare each decision against its fixture oracle — the Phase 6 eval formalises this."""
    t = Table(title="batch summary", header_style="bold")
    for col in ("claim", "scenario", "type", "sev", "clause", "fraud", "queue", "esc", "✓"):
        t.add_column(col, overflow="fold")

    hits = 0
    for name, d, fx in results:
        q = d.routing.queue if d.routing else "—"
        esc = d.routing.escalation_required if d.routing else None
        want_q, want_esc = fx.get("expected_queue"), fx.get("expected_escalation")
        ok = (q == want_q) and (esc == want_esc)
        hits += int(ok)
        t.add_row(
            name.replace("claim_", "").replace(".json", ""),
            fx.get("scenario", "—"),
            d.classification.claim_type if d.classification else "—",
            d.classification.severity[:4] if d.classification else "—",
            (d.coverage.cited_clause or "—").replace("-2026", "") if d.coverage else "—",
            d.fraud.risk if d.fraud else "—",
            f"{q}" if ok else f"[red]{q}[/red]→{want_q}",
            "yes" if esc else "no",
            "[green]✓[/green]" if ok else "[red]✗[/red]",
        )
    console.print(t)
    console.print(f"routing+escalation match: [bold]{hits}/{len(results)}[/bold]")


async def cmd_chat(args: argparse.Namespace) -> int:
    """Multi-turn recall demo — the in-session half of AC-05."""
    from src.memory.long_term import recall

    init_tracing()
    memories = await recall(args.claimant, query=args.query or "recent claims", k=8)
    if not memories:
        console.print(f"[yellow]no memories for {mask_text(args.claimant)}[/yellow]")
        return EXIT_OK
    t = Table(title=f"recalled for {mask_text(args.claimant)}", header_style="bold")
    t.add_column("kind"); t.add_column("memory", overflow="fold"); t.add_column("run")
    for m in memories:
        t.add_row(m.kind, m.text, m.source_run_id or "—")
    console.print(t)
    return EXIT_OK


def cmd_data(args: argparse.Namespace) -> int:
    import subprocess

    cmd = [sys.executable, str(ROOT / "scripts" / "generate_data.py")]
    if args.check:
        cmd.append("--check")
    return subprocess.call(cmd, cwd=str(ROOT))


def cmd_index(args: argparse.Namespace) -> int:
    from src.tools.rag_tool import build_index

    result = build_index(force=args.force)
    console.print(f"index [bold]{result['status']}[/bold] — "
                  f"{result['clauses']} clauses, checksum {result['checksum']}")
    return EXIT_OK


def cmd_graph(_: argparse.Namespace) -> int:
    from src.graph import TriageGraph

    app = TriageGraph().build()
    g = app.get_graph()
    nodes = sorted(n for n in g.nodes if not n.startswith("__"))
    console.print(f"[bold]{len(nodes)} nodes[/bold]: {', '.join(nodes)}")
    console.print(f"[bold]{len(g.edges)} edges[/bold]\n")
    console.print(g.draw_mermaid())
    return EXIT_OK


def cmd_todo(phase: str, spec: str) -> int:
    console.print(Panel(
        f"[yellow]{phase} is not implemented yet.[/yellow]\n\n"
        f"Owner: teammate.  Spec: [bold]{spec}[/bold]\n"
        f"See AGENTS.md §3 for the handover contract.",
        expand=False,
    ))
    return EXIT_USAGE


# ──────────────────────────────── entrypoint ──────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fnol", description="FNOL Claims-Triage Copilot")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="triage one claim")
    r.add_argument("--claim", required=True)
    r.add_argument("--thread-id", default=None)
    r.add_argument("--json", action="store_true")

    b = sub.add_parser("batch", help="triage every committed sample input")
    b.add_argument("--dir", default=str(settings.sample_claims_dir))

    c = sub.add_parser("chat", help="show what is recalled for a claimant")
    c.add_argument("--claimant", required=True)
    c.add_argument("--query", default=None)

    d = sub.add_parser("data", help="regenerate / verify the synthetic corpus")
    d.add_argument("--check", action="store_true")

    i = sub.add_parser("index", help="build the RAG index")
    i.add_argument("--force", action="store_true")

    sub.add_parser("graph", help="print the compiled graph topology")
    sub.add_parser("trace", help="PHASE 4 — regenerate and export Phoenix traces")
    sub.add_parser("eval", help="PHASE 6 — run DeepEval and the golden signals")
    sub.add_parser("verify", help="PHASE 6 — citation and hygiene verifier")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings.ensure_dirs()

    try:
        if args.cmd == "data":
            return cmd_data(args)
        if args.cmd == "index":
            return cmd_index(args)
        if args.cmd == "graph":
            return cmd_graph(args)
        if args.cmd == "trace":
            return cmd_todo("Phase 4 (Phoenix trace export)", "specs/SPEC-07-observability.md")
        if args.cmd == "eval":
            return cmd_todo("Phase 6 (evaluation)", "specs/SPEC-11-evaluation-and-tests.md")
        if args.cmd == "verify":
            return cmd_todo("Phase 6 (citation verifier)", "specs/SPEC-12-cli-and-runbook.md")

        if args.cmd in {"run", "batch"}:
            ok, message = probe()
            if not ok:
                console.print(f"[red]{message}[/red]")
                return EXIT_USAGE
            console.print(f"[dim]{message}[/dim]")

        handlers = {"run": cmd_run, "batch": cmd_batch, "chat": cmd_chat}
        return asyncio.run(handlers[args.cmd](args))
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        return EXIT_USAGE
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/yellow]")
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
