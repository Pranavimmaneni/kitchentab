# ADR-0001: PRD self-contradictions resolved

Date: 2026-10-09. Approved by Pranav in the kickoff review.

- Write kinds, idempotency and realtime follow PRD §21: ticks are `PUT` set-state with no
  `If-Match`; every create/transition `POST` needs `Idempotency-Key`; one SSE stream per house.
- Pairwise balances (FR-51) are netted per pair across all open bills and ledger entries; no
  simplification across three or more people.
- Bill-level amounts (tax, each fee, each discount, the adjustment) are rounded one at a time.
  `docs/reference/compute_shares_reference.py` is the definition.
- Milestone numbering follows PRD §17. Reminders are limited per member per bill per 48 h.
- PRD §11, §14A, §19.9 and §20.2 were edited so the document no longer contradicts itself.
