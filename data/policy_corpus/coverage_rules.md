# Underwriting Coverage Rules — Claims Handling Handbook (2026)

Served to the copilot as the MCP resource `policy://handbook/coverage-rules` (SPEC-05 §2.1).
Synthetic; for illustrative use only.

## 1. Severity bands

Severity is assessed from the estimated loss amount, with two overrides.

| Band | Estimated amount (INR) |
|---|---|
| minor | < 50,000 |
| moderate | 50,000 – 299,999 |
| major | 300,000 – 999,999 |
| catastrophic | >= 1,000,000 |

**Override A — injury.** Any claim reporting bodily injury is at least `major`.
**Override B — total loss.** Any claim where the estimated amount is >= 85% of the sum insured is at
least `major`.

## 2. Deductible application order

1. Assess the gross loss.
2. Apply any peril-specific exclusion. An exclusion defeats the claim entirely; do not proceed.
3. Apply the deductible to the assessed loss (once per claim).
4. Cap the result at the applicable clause limit, then at the sum insured.

Glass-only motor claims (AUTO-COMP-2026 §4.4) and fire damage to a dwelling (HOME-SHIELD-2026 §4.1)
are settled without a deductible.

## 3. Reporting deadlines by product

| Product | Deadline from date of loss | Clause |
|---|---|---|
| AUTO-COMP-2026 | 7 days | §2.1 |
| HOME-SHIELD-2026 | 14 days | §2.1 |
| LIAB-GEN-2026 | 30 days | §2.1 |

A notification beyond the deadline is a **late notification**: it does not by itself defeat the claim,
but it is recorded as a fraud indicator (FI-01) and the file is not eligible for fast-track.

## 4. Exclusion precedence

An exclusion in the exclusions section **overrides** any cover granted in the perils or own-damage
sections. Where a loss matches both a covering clause and an exclusion, the correct assessment is
`not_covered`, citing the **exclusion** clause — never the covering clause.

## 5. Period of insurance

A loss occurring outside the period of insurance, or while the policy is lapsed or cancelled, is
`not_covered`. Cite the period-of-insurance clause for the product
(AUTO-COMP-2026 §3.3, HOME-SHIELD-2026 §3.3).

## 6. Police report requirements

| Loss type | Report required | Clause |
|---|---|---|
| Motor theft | yes | AUTO-COMP-2026 §2.3 |
| Malicious damage (motor) | yes | AUTO-COMP-2026 §2.3 |
| Third-party bodily injury (motor) | yes | AUTO-COMP-2026 §2.3 |
| Burglary / housebreaking | yes, within 24 hours | HOME-SHIELD-2026 §2.3 |

A claim of one of these types without a police report is `partially_covered` at best, and is recorded
as fraud indicator FI-07.

## 7. Handling routes

| Route | Meaning |
|---|---|
| fast_track | Straight-through assessment by a junior handler; low value, clean coverage, low fraud risk. |
| standard | Full desk assessment by a claims handler. |
| investigate | Referred to the Special Investigation Unit before any assessment. |

**Escalation is mandatory** — regardless of route — for suspected fraud, for a claim at or above the
high-value threshold, and where coverage cannot be determined. An escalated claim is a recommendation
to a human handler and is never auto-approved.
