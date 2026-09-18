"""Threat detection over untrusted claimant text (SPEC-03 §2.4).

Detection annotates; it does not decide. A claim carrying an injection payload is still triaged —
the narrative is real evidence about the loss — but the injected instruction has no effect, because
the router never sees free text at all (design.md §D2 / SPEC-03 §2.1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ThreatKind = str  # instruction_injection | role_manipulation | data_exfiltration |
#                   output_manipulation | delimiter_escape


@dataclass(frozen=True)
class Threat:
    kind: ThreatKind
    pattern: str
    excerpt: str


_PATTERNS: list[tuple[ThreatKind, str]] = [
    # instruction injection
    ("instruction_injection", r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?"),
    ("instruction_injection", r"disregard\s+(the\s+)?(policy|rules?|guidelines?|instructions?)"),
    ("instruction_injection", r"\bnew\s+instructions?\s*:"),
    ("instruction_injection", r"^\s*(system|assistant|developer)\s*:", ),
    ("instruction_injection", r"forget\s+everything"),
    # role manipulation
    ("role_manipulation", r"you\s+are\s+now\b"),
    ("role_manipulation", r"\bact\s+as\s+(an?\s+)?(admin|adjuster|supervisor|underwriter)"),
    ("role_manipulation", r"\bpretend\s+(to\s+be|you)\b"),
    ("role_manipulation", r"\bdeveloper\s+mode\b"),
    # data exfiltration
    ("data_exfiltration", r"\b(list|show|give|send)\s+(me\s+)?all\s+claims?\b"),
    ("data_exfiltration", r"\bCLT-\d{4,}\b"),          # naming another claimant id in free text
    ("data_exfiltration", r"\bPOL-[A-Z]{2}-\d{4,}\b"),  # naming a policy number in free text
    ("data_exfiltration", r"what\s+is\s+the\s+policy\s+number\s+for"),
    ("data_exfiltration", r"\bother\s+claimants?\b"),
    ("data_exfiltration", r"\bdatabase\b|\bsystem\s+prompt\b"),
    # output manipulation
    ("output_manipulation", r"\b(always\s+)?(return|set|mark)\s+.{0,20}(fast[\s_-]?track|approved)"),
    ("output_manipulation", r"fraud\s+risk\s+(to|=|is)\s+low"),
    ("output_manipulation", r"\bauto[\s_-]?approve\b"),
    ("output_manipulation", r"\bdo\s+not\s+escalate\b"),
    # delimiter escape
    ("delimiter_escape", r"<<<\s*END_UNTRUSTED"),
    ("delimiter_escape", r"<<<UNTRUSTED"),
    ("delimiter_escape", r"```\s*(system|assistant)"),
    ("delimiter_escape", r"</?(system|instructions?)>"),
]

_COMPILED = [(kind, re.compile(pat, re.IGNORECASE | re.MULTILINE)) for kind, pat in _PATTERNS]


def detect_threats(text: str) -> list[Threat]:
    """Return every pattern family that fires on `text`, with the matching excerpt."""
    found: list[Threat] = []
    seen: set[tuple[str, str]] = set()
    for kind, rx in _COMPILED:
        m = rx.search(text)
        if not m:
            continue
        key = (kind, rx.pattern)
        if key in seen:
            continue
        seen.add(key)
        start = max(m.start() - 30, 0)
        end = min(m.end() + 30, len(text))
        found.append(Threat(kind=kind, pattern=rx.pattern, excerpt=text[start:end].strip()))
    return found


def names_foreign_claimant(text: str, own_claimant_id: str) -> bool:
    """True when the text names a claimant id that is not the one filing this claim (AC-06)."""
    for m in re.finditer(r"\bCLT-\d{4,}\b", text):
        if m.group(0) != own_claimant_id:
            return True
    return False
