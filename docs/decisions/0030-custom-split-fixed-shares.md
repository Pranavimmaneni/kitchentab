# ADR-0030: Custom split on asks is a fixed share of the line

Status: accepted · 2026-10-10 · PRD §6, §19.4, FR-81 · TEST_PLAN MONEY-11, ASK-15..ASK-17

## Context

The household asked for a custom split when an owner says yes to an ask: quick choices 25% / 50% /
100% plus a custom amount. The owner's answer on how it works: "split either by percentage or add
their own value". Until now "Add to split" added the asker with weight 1 (an equal share) and
"Chip in" (settled tabs) took a free amount.

Allocation weights are `numeric(6,3)`. Expressing "Kabir pays 25%" as a weight next to the owners'
weights (w = p·W / (1 − p)) is not exact with three decimals (1/3 → 0.333 gives $24.98 of $100), and
an owner's own value ($5.00 of $11.37) generally has no three-decimal weight at all. Rescaling the
owners' weights would make a later tick (weight 1) next to them meaningless.

## Decision

1. **An allocation can hold a fixed share of its line** instead of a weight: `share_num/share_den`
   (0 < share ≤ 1), two nullable `bigint` columns on `allocations` (migration 0007, additive).
   A percent p is stored as p/100; an owner's own value of X cents on a line of L cents as X/L
   (reduced), so the asker's line share is exactly X cents.
2. **`compute_shares` (PRD §6 step 1)**: fixed shares take their exact fraction; weighted allocations
   split 1 − Σf by weight. If Σf ≥ 1 or nobody is weighted, the fixed shares split the whole line in
   proportion and the weighted allocations get 0 (they stay on the line). The exact parts go through
   the same largest-remainder rounding, so the invariant is untouched. Python and TypeScript scale
   everything to one common denominator (integers / BigInt); the reference keeps Fractions.
3. **Price edits follow the fraction.** If the payer edits the line later, the asker keeps the same
   fraction (25% stays 25%; $5.00 of $11.37 stays 500/1137 of the new price). A later tick of someone
   else (weight 1) shares the owners' part, not the asker's.
4. **100%**: the asker pays the whole line; the owners stay ticked at $0.00 and stay co-owners in the
   kitchen (removing them would change ownership and complicate Undo).
5. **Settled tab**: the same choices set the chip-in amount: percent of the line total with
   largest-remainder against the owners' part (the asker wins a tie: 50% of $11.37 = $5.69), or the
   owner's own value ($0.01 up to the line total). Ledger entries as before.
6. **Preview first**: `GET /requests/{rid}/answer-preview` returns the asker's part and every share
   delta (nothing saved); the UI shows it before "Say yes". Undo removes the allocation as before.
7. "Equal share" (weight 1) stays the default choice, so FR-73 and the `request_add_to_split`
   fixture are unchanged.

## Consequences

- Old code on the new schema ignores the columns (weight 1 for a fixed-share allocation) until the
  new code is deployed; `make migrate-prod` runs before the merge, and nothing writes shares before
  the new code is live.
- The split grid shows "CUSTOM SPLIT · KABIR" on such a line instead of "$x × n".
- The 10,000-bill corpus digest (MONEY-03) is unchanged; fixed shares are covered by new fixtures and
  a Python property test against the reference (MONEY-11).
