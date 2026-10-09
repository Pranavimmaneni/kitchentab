# ADR-0003: Member-or-guest references and schema gap fills

- Columns that point at a member or a guest (`allocations`, `payments`, `share_snapshots`) are two
  nullable FKs `member_id` / `guest_id` with `CHECK num_nonnulls(...) = 1` and partial unique
  indexes, so `ON DELETE RESTRICT` works on money tables.
- `users.timezone` (quiet hours, reminders). `notifications` gains `dedupe_key` (unique), status,
  attempts, `next_attempt_at`, `last_error`.
- Refunds are `payments.direction = refund`, not a status. Confirmed payments on voided bills
  become `ledger_entries.source = void_credit`.
- `members.anonymized_at`: anonymized members are excluded from the unique-name index, so several
  "Former roommate" rows can coexist. Rejoining reactivates the existing member row.
- Exactly one admin: the index guarantees at most one; services require an admin transfer before
  the admin leaves, is removed, or deletes their account.
- CHECK constraints are written NULL-safe (`coalesce`, `IS NOT DISTINCT FROM`); a test proves each
  rejects bad rows.
