# ADR-0018: Derived link tokens

PRD §8 stores link tokens only as hashes, but the House screen (§14A) shows the invite link again
and the share card shows the bill link later. Decision: tokens are derived, not stored:
`token = base62(HMAC-SHA256(TOKEN_HASH_PEPPER, "derive:{kind}:{id}:{generation}"))[:22]`, where
`generation` is the creation time and changes on rotation. Only `HMAC(pepper, token)` is stored and
used for lookup. Members recompute the link when they need to show it. Rotating the invite changes
the generation, so the old link returns 410. Token entropy stays at least 128 bits. Leaking the
pepper would expose links, which is the same exposure as leaking the hash pepper today.
