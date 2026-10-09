"""Deterministic random-bill corpus for MONEY-03 (Python == TypeScript == reference).

Both languages implement this exact generator (TS: `apps/web/lib/money/corpus.ts`). Each computes
shares for every bill and hashes the results; the digest must equal `money_property_digest.json`,
which `python packages/fixtures/money_corpus.py --write` produces from the reference script.

Generator (all integer math, uint32 mulberry32):
  rng = mulberry32(seed); r(lo, hi) = lo + next_u32() % (hi - lo + 1)
  bill i: n = r(1, 20) participants "p00".."p{n-1}", sort_order_j = r(0, 3)  (ties broken by id)
          items m = r(1, 200) if r(0, 49) == 0 else r(1, 40)
          line total: k = r(0, 19); 0 if k == 0, r(-2000, -1) if k == 1, else r(1, 50000)
          allocations: c = r(1, n); participants picked by c partial Fisher-Yates swaps over 0..n-1
                       (j from 0 to c-1: s = r(j, n-1); swap); weight_milli = 1000 if r(0, 4) else r(1, 5000)
          tax = r(0, 5000); f = r(0, 3) fees, kind_t = r(0, 9):
                0..4 fee, 5..6 tip, 7..8 order_discount (amount r(0, 3000)), 9 adjustment (amount r(-500, 500))
          total = Σ line totals + tax + Σ signed fees
Digest: sha256 over lines "i:" + ",".join(f"{pid}={cents}" for pid in sorted ids) joined by "\n".
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

SEED = 20261009
COUNT = 10_000
HERE = Path(__file__).resolve().parent
DIGEST_FILE = HERE / "money_property_digest.json"


class Mulberry32:
    def __init__(self, seed: int) -> None:
        self.state = seed & 0xFFFFFFFF

    def next_u32(self) -> int:
        self.state = (self.state + 0x6D2B79F5) & 0xFFFFFFFF
        t = self.state
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        return (t ^ (t >> 14)) & 0xFFFFFFFF

    def r(self, lo: int, hi: int) -> int:
        return lo + self.next_u32() % (hi - lo + 1)


FEE_KINDS = ["fee"] * 5 + ["tip"] * 2 + ["order_discount"] * 2 + ["adjustment"]


def generate(seed: int = SEED, count: int = COUNT) -> Iterator[dict[str, Any]]:
    rng = Mulberry32(seed)
    for _ in range(count):
        n = rng.r(1, 20)
        participants = [{"id": f"p{j:02d}", "sort_order": rng.r(0, 3)} for j in range(n)]
        m = rng.r(1, 200) if rng.r(0, 49) == 0 else rng.r(1, 40)
        items = []
        total = 0
        for k in range(m):
            kind = rng.r(0, 19)
            cents = 0 if kind == 0 else rng.r(-2000, -1) if kind == 1 else rng.r(1, 50000)
            c = rng.r(1, n)
            idx = list(range(n))
            for j in range(c):
                s = rng.r(j, n - 1)
                idx[j], idx[s] = idx[s], idx[j]
            allocations = []
            for j in range(c):
                weight_milli = 1000 if rng.r(0, 4) else rng.r(1, 5000)
                allocations.append({"participant": f"p{idx[j]:02d}", "weight_milli": weight_milli})
            items.append({"id": f"i{k}", "line_total_cents": cents, "allocations": allocations})
            total += cents
        tax = rng.r(0, 5000)
        total += tax
        fees = []
        for f in range(rng.r(0, 3)):
            kind = FEE_KINDS[rng.r(0, 9)]
            amount = rng.r(-500, 500) if kind == "adjustment" else rng.r(0, 3000)
            fees.append({"kind": kind, "label": f"f{f}", "amount_cents": amount})
            total += -amount if kind == "order_discount" else amount
        yield {"participants": participants, "items": items, "tax_cents": tax, "fees": fees, "total_cents": total}


def to_reference_shape(bill: dict[str, Any]) -> dict[str, Any]:
    """Reference script takes decimal weights; convert thousandths to exact decimal strings."""
    out = dict(bill)
    out["items"] = [
        {
            **item,
            "allocations": [
                {"participant": a["participant"], "weight": f"{a['weight_milli'] // 1000}.{a['weight_milli'] % 1000:03d}"}
                for a in item["allocations"]
            ],
        }
        for item in bill["items"]
    ]
    return out


def digest(results: Iterator[dict[str, int]]) -> str:
    h = hashlib.sha256()
    for i, shares in enumerate(results):
        line = f"{i}:" + ",".join(f"{pid}={shares[pid]}" for pid in sorted(shares))
        h.update(line.encode() + b"\n")
    return h.hexdigest()


def reference_digest(seed: int = SEED, count: int = COUNT) -> str:
    sys.path.insert(0, str(HERE.parents[1] / "docs" / "reference"))
    import compute_shares_reference as ref

    return digest(ref.compute_shares(to_reference_shape(b))["shares"] for b in generate(seed, count))


if __name__ == "__main__":
    if "--check" in sys.argv:
        stored = json.loads(DIGEST_FILE.read_text(encoding="utf-8"))
        ok = stored == {"seed": SEED, "count": COUNT, "sha256": reference_digest()}
        print("ok: corpus digest matches the reference" if ok else "FAIL: corpus digest differs from the reference")
        sys.exit(0 if ok else 1)
    if "--write" in sys.argv:
        value = {"seed": SEED, "count": COUNT, "sha256": reference_digest()}
        DIGEST_FILE.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {DIGEST_FILE}: {value}")
    else:
        print(json.dumps({"seed": SEED, "count": COUNT, "sha256": reference_digest()}))
