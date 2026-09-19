"""Free-text PII detection and redaction with Microsoft Presidio (SPEC-09 §2.2).

Layered *on top of* the deterministic identifier masking in `src/security/masking.py`: that layer
handles the identifiers we mint (POL-…, CLT-…) with exact regexes; this one catches what a claimant
types in free text — names, phone numbers in odd shapes, e-mail addresses, card numbers.

Uses the small spaCy model (`en_core_web_sm`, pinned in requirements.txt) so it installs with pip and
runs on CPU. If Presidio or the model is missing, `redact()` degrades to the regex layer and says so
in its result (NFR-04) — PII protection never silently disappears.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from src.security.masking import mask_text

log = logging.getLogger(__name__)

# Entities worth redacting in an FNOL narrative. LOCATION and DATE_TIME are deliberately absent:
# where and when the loss happened are the facts the classifier and coverage agents need.
ENTITIES = ["PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "CREDIT_CARD", "IBAN_CODE", "IP_ADDRESS"]
MIN_SCORE = 0.6


@dataclass
class PIIResult:
    text: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    engine: str = "presidio"

    @property
    def found(self) -> bool:
        return bool(self.findings)


@lru_cache(maxsize=1)
def _engines() -> tuple[Any, Any] | None:
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine

        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        })
        analyzer = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
        # The corpus is Indian: add India-region phone parsing and the common "98765 43210" mobile
        # layout, which the default recognizers miss.
        from presidio_analyzer import Pattern, PatternRecognizer
        from presidio_analyzer.predefined_recognizers import PhoneRecognizer

        analyzer.registry.add_recognizer(PhoneRecognizer(supported_regions=("IN", "GB", "US")))
        analyzer.registry.add_recognizer(PatternRecognizer(
            supported_entity="PHONE_NUMBER", name="IndianMobileRecognizer",
            patterns=[Pattern("in_mobile", r"(?<![\d-])(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?![\d-])",
                              0.7)],
        ))
        return analyzer, AnonymizerEngine()
    except Exception as exc:  # noqa: BLE001 - degrade to the regex layer, never crash
        log.warning("Presidio unavailable, falling back to regex masking: %s", exc)
        return None


def presidio_available() -> bool:
    return _engines() is not None


def redact(text: str) -> PIIResult:
    """Return `text` with free-text PII replaced by `<ENTITY>` placeholders, plus what was found."""
    if not text:
        return PIIResult(text=text)
    engines = _engines()
    if engines is None:
        masked = mask_text(text)
        findings = [{"entity": "IDENTIFIER", "score": 1.0}] if masked != text else []
        return PIIResult(text=masked, findings=findings, engine="regex-fallback")

    analyzer, anonymizer = engines
    results = [
        r for r in analyzer.analyze(text=text, language="en", entities=ENTITIES)
        if r.score >= MIN_SCORE
    ]
    if not results:
        return PIIResult(text=mask_text(text))
    anonymized = anonymizer.anonymize(text=text, analyzer_results=results)
    findings = [
        # never record the matched value itself — only what kind of thing it was and where
        {"entity": r.entity_type, "score": round(r.score, 2), "start": r.start, "end": r.end}
        for r in results
    ]
    return PIIResult(text=mask_text(anonymized.text), findings=findings)
