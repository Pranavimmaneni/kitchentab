# ADR-0002: Reconciliation override as a signed adjustment

FR-13 lets the payer use the receipt total anyway, but §6 refuses any bill whose shares do not sum
to its total. Decision: store the printed total in `bills.printed_total_cents` (equal to
`total_cents`, with a required `reconcile_override_reason`) and add one `bill_fees` row of kind
`adjustment`, stored signed, for the difference. It is allocated like tax by clipped subtotal, so
the invariant still holds. A partial unique index allows one adjustment per bill. Fixtures
`reconciliation_adjustment` and `reconciliation_adjustment_negative` pin the math.
