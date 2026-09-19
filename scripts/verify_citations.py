"""Citation + hygiene verifier — Rules R1, R2, R4, NFR-01, NFR-05 (SPEC-12 §2.4).

    python scripts/verify_citations.py          # or: python -m src.main verify

Exits non-zero on any failure. Checks:
  1. every repo path cited in docs/*.md and README.md exists (and a cited `::symbol` is defined in it);
     every cited guard rule id (IG-xx / OG-xx) is implemented in src/guardrails/;
  2. every run_id / span_id cited in docs/ resolves in traces/phoenix_spans.parquet, and every
     `logs/<file>.jsonl#L<n>` record citation points at an existing line;
  3. every evidence file has its committed producer (scripts/evidence_manifest.py);
  4. tool_name values in logs/tool_calls.jsonl ⊆ the registry / MCP server tool names (AC-07);
  5. no secret pattern in any tracked file; no plaintext policy number / claimant id under logs/,
     traces/, reports/;
  6. Rule R4 — no other-provider import in our code, no other-provider direct dependency, no other
     provider key in .env.example, no Docker/compose/k8s manifests.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evidence_manifest import EVIDENCE, OPTIONAL  # noqa: E402

DOCS = sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md"]
PATH_RE = re.compile(
    r"`((?:src|tests|scripts|logs|traces|reports|docs|data|mcp_server|specs)/[\w./\-§ ]+?)"
    r"(?:::([\w.]+))?(?:#L(\d+)|#[\w\-]+)?(?::\d+)?`"
)
RUN_RE = re.compile(r"\brun-[0-9a-f]{12}\b")
SPAN_RE = re.compile(r"`([0-9a-f]{16})`")  # every back-ticked 16-hex id is a span citation
RULE_RE = re.compile(r"\b((?:IG|OG)-0\d)\b")
SECRET_RES = [
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token)\s*[=:]\s*['\"][A-Za-z0-9_\-]{20,}['\"]"),
]
FOREIGN_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(openai|anthropic|langchain_openai|langchain_anthropic|cohere|"
    r"mistralai|groq)\b", re.MULTILINE)
FOREIGN_PKGS = {"openai", "anthropic", "langchain-openai", "langchain-anthropic", "cohere",
                "mistralai", "groq", "langchain-groq"}

failures: list[str] = []
passes: list[str] = []


def fail(check: str, msg: str) -> None:
    failures.append(f"[{check}] {msg}")


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
        return [ROOT / p for p in out.splitlines() if p and (ROOT / p).is_file()]
    except Exception:  # noqa: BLE001
        return [p for p in ROOT.rglob("*") if p.is_file() and ".venv" not in p.parts
                and ".git" not in p.parts]


# ──────────────────────────────── check 1 ─────────────────────────────────────


def check_paths() -> None:
    guard_src = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "src/guardrails").glob("*.py"))
    n = 0
    for doc in DOCS:
        if not doc.exists():
            continue
        text = doc.read_text(encoding="utf-8")
        for m in PATH_RE.finditer(text):
            rel, symbol, line = m.group(1).rstrip("/."), m.group(2), m.group(3)
            n += 1
            target = ROOT / rel
            if any(ch in rel for ch in "*<>{}"):
                continue  # a glob or placeholder, not a citation
            if not target.exists():
                fail("1-paths", f"{doc.name}: cited path does not exist: {rel}")
                continue
            if symbol and target.is_file():
                name = symbol.split(".")[-1]
                src = target.read_text(encoding="utf-8", errors="ignore")
                if not re.search(rf"\b(def|class|async def)\s+{re.escape(name)}\b|^{re.escape(name)}\s*=",
                                 src, re.MULTILINE):
                    fail("1-paths", f"{doc.name}: `{rel}::{symbol}` — symbol not defined")
            if line and target.is_file():
                total = sum(1 for _ in target.open(encoding="utf-8", errors="ignore"))
                if int(line) > total:
                    fail("2-records", f"{doc.name}: {rel}#L{line} beyond end of file ({total})")
        for rid in set(RULE_RE.findall(text)):
            if rid not in guard_src:
                fail("1-paths", f"{doc.name}: guard rule {rid} not implemented in src/guardrails/")
    passes.append(f"1-paths: {n} path citations checked across {len(DOCS)} documents")


# ──────────────────────────────── check 2 ─────────────────────────────────────


def check_trace_ids() -> None:
    parquet = ROOT / "traces/phoenix_spans.parquet"
    cited_runs: set[str] = set()
    cited_spans: set[str] = set()
    for doc in DOCS:
        if doc.exists():
            text = doc.read_text(encoding="utf-8")
            cited_runs |= set(RUN_RE.findall(text))
            cited_spans |= set(SPAN_RE.findall(text))
    if not (cited_runs or cited_spans):
        fail("2-trace-ids", "no run_id / span_id cited anywhere in docs/ (AC-08)")
        return
    if not parquet.exists():
        fail("2-trace-ids", "traces/phoenix_spans.parquet missing")
        return
    import pandas as pd

    df = pd.read_parquet(parquet, columns=["context.span_id", "run_id"])
    spans, runs = set(df["context.span_id"].astype(str)), set(df["run_id"].dropna().astype(str))
    # run ids are also minted for runs whose evidence is a log record rather than a span
    log_runs: set[str] = set()
    for f in ("logs/tool_calls.jsonl", "logs/agent_actions.jsonl"):
        p = ROOT / f
        if p.exists():
            log_runs |= set(RUN_RE.findall(p.read_text(encoding="utf-8")))
    for r in sorted(cited_runs - runs - log_runs):
        fail("2-trace-ids", f"cited run_id {r} not found in the parquet or the logs")
    for s in sorted(cited_spans - spans):
        fail("2-trace-ids", f"cited span_id {s} not found in traces/phoenix_spans.parquet")
    passes.append(f"2-trace-ids: {len(cited_runs)} run_id(s), {len(cited_spans)} span_id(s) resolve")


# ──────────────────────────────── check 3 ─────────────────────────────────────


def check_producers() -> None:
    for artifact, producers in {**EVIDENCE, **OPTIONAL}.items():
        present = (ROOT / artifact).exists()
        if not present:
            if artifact in EVIDENCE:
                fail("3-producers", f"evidence missing: {artifact}")
            continue
        for p in producers:
            if not (ROOT / p).exists():
                fail("3-producers", f"{artifact}: producer {p} missing")
    passes.append(f"3-producers: {len(EVIDENCE)} required artifacts paired with producers")


# ──────────────────────────────── check 4 ─────────────────────────────────────


def check_tool_names() -> None:
    from src.mcp_client import MCP_TOOL_NAMES
    from src.tools import rag_tool  # noqa: F401 - registers the RAG tool
    from src.tools import registry

    server = (ROOT / "mcp_server/server.py").read_text(encoding="utf-8")
    server_tools = set(re.findall(r"@mcp\.tool\(\)\s*\ndef\s+(\w+)", server))
    if set(MCP_TOOL_NAMES) != server_tools:
        fail("4-tools", f"MCP client names {sorted(MCP_TOOL_NAMES)} != server {sorted(server_tools)}")
    known = set(registry.list_tools()) | server_tools
    log = ROOT / "logs/tool_calls.jsonl"
    if not log.exists():
        fail("4-tools", "logs/tool_calls.jsonl missing")
        return
    names = {json.loads(ln)["tool_name"] for ln in log.read_text(encoding="utf-8").splitlines()
             if ln.strip()}
    if not names <= known:
        fail("4-tools", f"unreconciled tool names in the log: {sorted(names - known)}")
    passes.append(f"4-tools: log names {sorted(names)} ⊆ code {sorted(known)}")


# ──────────────────────────────── check 5 ─────────────────────────────────────


def check_secrets_and_identifiers() -> None:
    from src.security.masking import contains_unmasked_identifier

    files = tracked_files()
    for f in files:
        if f.suffix in {".parquet", ".png", ".sqlite", ".bin", ".pyc", ".docx"} or f.name == ".env":
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for rx in SECRET_RES:
            if rx.search(text):
                fail("5-secrets", f"secret-shaped string in {f.relative_to(ROOT)}")
    evidence_dirs = ("logs", "traces", "reports")
    for f in files:
        rel = f.relative_to(ROOT)
        if rel.parts[0] not in evidence_dirs:
            continue
        if f.suffix == ".parquet":
            import pandas as pd

            text = pd.read_parquet(f).to_csv(index=False)
        elif f.suffix in {".png"}:
            continue
        else:
            text = f.read_text(encoding="utf-8", errors="ignore")
        if contains_unmasked_identifier(text):
            fail("5-identifiers", f"plaintext policy number / claimant id in {rel}")
    passes.append(f"5-secrets: {len(files)} files scanned; evidence dirs identifier-clean")


# ──────────────────────────────── check 6 ─────────────────────────────────────


def check_provider_rule() -> None:
    for d in ("src", "mcp_server", "scripts", "tests"):
        for f in (ROOT / d).rglob("*.py"):
            m = FOREIGN_IMPORT.search(f.read_text(encoding="utf-8", errors="ignore"))
            if m:
                fail("6-R4", f"{f.relative_to(ROOT)} imports {m.group(1)}")
    reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    for ln in reqs:
        pkg = re.split(r"[<>=\[ @;]", ln.strip(), maxsplit=1)[0].lower()
        if pkg in FOREIGN_PKGS:
            fail("6-R4", f"requirements.txt directly depends on {pkg}")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        if re.search(rf"^\s*{key}\s*=", env_example, re.MULTILINE):
            fail("6-R4", f".env.example configures {key}")
    for pat in ("Dockerfile*", "docker-compose*", "compose.y*ml", "*.dockerfile"):
        for f in ROOT.glob(pat):
            fail("6-R4", f"container manifest present: {f.name}")
    if not re.search(r"^\.env$", (ROOT / ".gitignore").read_text(encoding="utf-8"), re.MULTILINE):
        fail("6-R4", ".gitignore does not cover .env (NFR-01)")
    passes.append("6-R4: Gemini-only imports and direct deps; no container manifests; .env ignored")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for check in (check_paths, check_trace_ids, check_producers, check_tool_names,
                  check_secrets_and_identifiers, check_provider_rule):
        try:
            check()
        except Exception as exc:  # noqa: BLE001 - a crashing check is a failing check
            fail(check.__name__, f"{type(exc).__name__}: {exc}")
    for p in passes:
        print("PASS", p)
    for f in failures:
        print("FAIL", f)
    print(f"\n{'OK' if not failures else 'FAILED'} — {len(passes)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
