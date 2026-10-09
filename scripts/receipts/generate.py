"""Generate the synthetic receipt fixtures (BUILD_ALL C1, ADR-0020).

    cd apps/api && uv run python ../../scripts/receipts/generate.py [--skip-render] [--only r01,i01]

Writes `packages/fixtures/receipts/`:
  r01..r15.pdf          text PDFs (Chromium page.pdf) across the 8 store profiles + edge cases
  i01..i15.{jpg,png,pdf} noisy photos / image-only PDFs made from the same receipts (Pillow)
  <id>.truth.json       ground truth in the ExtractedBill shape
  manifest.json         what each fixture is, its files, noise settings and expected flags

Everything is seeded (SEED below), so a rerun produces the same receipts, truth and noise. Text
PDFs differ only in their embedded creation timestamp. Then run `node scripts/receipts/ocr.mjs`
to (re)build the `<id>.ocr.json` files for the image fixtures.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "apps" / "api"))
sys.path.insert(0, str(HERE))

from app.domain.kitchen import default_shelf_life
from app.extraction.schema import (
    ExtractedBill,
    ExtractedFee,
    ExtractedItem,
)
from catalog import BY_KEY, Product

SEED = 20261002
OUT = REPO / "packages" / "fixtures" / "receipts"
BUILD = REPO / ".cache" / "receipts-build"
NL = chr(10)


# --- money helpers ---------------------------------------------------------------------------------


def m(cents: int, dollar: bool = False) -> str:
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}{'$' if dollar else ''}{c // 100}.{c % 100:02d}"


def mul(q: Decimal, unit: int) -> int:
    return int((q * unit).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def pct_of(base: int, rate_milli_pct: int) -> int:
    """rate in thousandths of a percent (6.625% → 6625)."""
    return int(
        (Decimal(base) * rate_milli_pct / 100000).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
    )


# --- receipt model ---------------------------------------------------------------------------------


@dataclass
class Entry:
    product: Product
    qty: int = 1
    weight: Decimal | None = None
    unit: int | None = None  # price override (cents per unit / per lb)
    discount: tuple[str, int] | None = None  # (label, cents)
    refund: bool = False
    zero: bool = False
    deposit: int = 0  # bottle deposit printed right after the item (a fee)
    brand: bool = False
    long_name: str | None = None  # printed/truth override for long wrapped names
    code: str = ""  # item number / UPC / DPCI assigned by the profile

    @property
    def unit_cents(self) -> int:
        if self.zero:
            return 0
        return self.unit if self.unit is not None else self.product.price

    @property
    def quantity(self) -> Decimal:
        return self.weight if self.weight is not None else Decimal(self.qty)

    @property
    def total(self) -> int:
        t = mul(self.quantity, self.unit_cents)
        return -t if self.refund else t

    @property
    def taxable(self) -> bool:
        return self.product.category == "household" or self.product.icon in (
            "soda",
            "water",
        )

    @property
    def net(self) -> int:
        return self.total - (self.discount[1] if self.discount else 0)


@dataclass
class Receipt:
    rid: str
    profile: str
    date: str  # YYYY-MM-DD
    entries: list[Entry]
    tax_rate: int = 6625  # thousandths of a percent
    fees: list[tuple[str, str, int]] = field(
        default_factory=list
    )  # (kind, label, cents) summary fees
    order_discounts: list[tuple[str, int]] = field(default_factory=list)
    mismatch: int = 0  # printed total = true total + mismatch
    notes: list[str] = field(default_factory=list)

    @property
    def deposits(self) -> int:
        return sum(e.deposit for e in self.entries)

    @property
    def item_net(self) -> int:
        return sum(e.net for e in self.entries)

    @property
    def tax(self) -> int:
        return pct_of(sum(e.net for e in self.entries if e.taxable), self.tax_rate)

    @property
    def total(self) -> int:
        fees = sum(c for _, _, c in self.fees) + self.deposits
        return self.item_net + self.tax + fees - sum(c for _, c in self.order_discounts)


BRANDS = {
    "walmart": ("GV", "Great Value"),
    "costco": ("KS", "Kirkland Signature"),
    "target": ("GG", "Good & Gather"),
}
THERMAL = {"walmart", "target", "costco", "shoprite", "traderjoes", "hmart"}
STORE_NAMES = {
    "walmart": "Walmart",
    "target": "Target",
    "costco": "Costco",
    "shoprite": "ShopRite",
    "traderjoes": "Trader Joe's",
    "hmart": "H Mart",
    "instacart": "Instacart",
    "amazon": "Whole Foods Market",
}


def truth_name(r: Receipt, e: Entry) -> str:
    if e.long_name:
        return e.long_name
    if r.profile in THERMAL:
        name = e.product.name
        if e.brand and r.profile in BRANDS:
            name = f"{BRANDS[r.profile][1]} {name[0].lower()}{name[1:]}"
        return name
    if r.profile == "amazon" and e.brand:
        return f"365 by Whole Foods Market, {e.product.long}"
    return e.product.long


def printed_caps(r: Receipt, e: Entry) -> str:
    if e.long_name:
        return e.long_name.upper()
    name = e.product.abbr
    if e.brand and r.profile in BRANDS:
        name = f"{BRANDS[r.profile][0]} {name}"
    return name


def truth_bill(r: Receipt) -> ExtractedBill:
    items = []
    for e in r.entries:
        p = e.product
        items.append(
            ExtractedItem(
                name=truth_name(r, e),
                raw_text=printed_caps(r, e)
                if r.profile in THERMAL
                else truth_name(r, e),
                quantity=e.quantity,
                unit_price_cents=e.unit_cents,
                line_total_cents=e.total,
                discount_cents=e.discount[1] if e.discount else 0,
                taxable=e.taxable,
                confidence_x1000=1000,
                storage=p.storage,  # type: ignore[arg-type]
                category=p.category,  # type: ignore[arg-type]
                icon_key=p.icon,
                shelf_life_days=None
                if p.category == "household"
                else default_shelf_life(p.category, p.icon),
            )
        )
    fees = (
        [ExtractedFee(kind="fee", label="Bottle deposit", amount_cents=r.deposits)]
        if r.deposits
        else []
    )
    fees += [ExtractedFee(kind=k, label=lbl, amount_cents=c) for k, lbl, c in r.fees]  # type: ignore[arg-type]
    fees += [
        ExtractedFee(kind="order_discount", label=lbl, amount_cents=c)
        for lbl, c in r.order_discounts
    ]
    bill = ExtractedBill(
        store_name=STORE_NAMES[r.profile],
        purchase_date=r.date,  # type: ignore[arg-type]
        items=items,
        subtotal_cents=subtotal_printed(r),
        tax_cents=r.tax,
        fees=fees,
        total_cents=r.total + r.mismatch,
        field_confidence_x1000={
            "store_name": 1000,
            "purchase_date": 1000,
            "total": 1000,
        },
        source="pdf_text",
    )
    return bill.recompute()


def subtotal_printed(r: Receipt) -> int:
    # Thermal registers ring deposits and bag fees as lines, so their subtotal includes them.
    if r.profile in THERMAL:
        return (
            r.item_net
            + r.deposits
            + sum(c for k, lbl, c in r.fees if "bag" in lbl.lower())
        )
    return r.item_net


# --- thermal layout helpers ------------------------------------------------------------------------


def row(left: str, right: str, width: int) -> list[str]:
    """Right-align `right`; wrap a long `left` at word boundaries, price on the last line."""
    room = width - len(right) - 2
    words = left.split(" ")
    lines: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > room:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    lines.append(cur)
    last = lines[-1]
    lines[-1] = last + " " * (width - len(last) - len(right)) + right
    return lines


def center(text: str, width: int) -> str:
    pad = max((width - len(text)) // 2, 0)
    return " " * pad + text


def thermal_html(
    title: str, lines: list[str], width: int, *, big: str, sub: list[str]
) -> str:
    rows = "\n".join(
        f'<div class="l">{html.escape(ln) if ln else "&nbsp;"}</div>' for ln in lines
    )
    subs = "\n".join(f'<div class="s">{html.escape(s)}</div>' for s in sub)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
html,body{{margin:0;background:#fbfaf6}}
body{{width:{width * 9 + 40}px;padding:18px 20px 28px;box-sizing:border-box;color:#1b1b1b;
font-family:'Courier New',Courier,monospace;font-size:15px;line-height:21px}}
.big{{font-size:30px;line-height:36px;font-weight:700;text-align:center;letter-spacing:1px}}
.s{{text-align:center}}
.l{{white-space:pre;break-inside:avoid}}
</style></head><body><div class="big">{html.escape(big)}</div>{subs}
{rows}
</body></html>"""


# --- profiles: thermal ------------------------------------------------------------------------------


def money_tail(
    cents: int, code: str = "", dollar: bool = False, trailing_minus: bool = False
) -> str:
    if cents < 0 and trailing_minus:
        s = f"{m(-cents, dollar)}-"
    else:
        s = m(cents, dollar)
    return f"{s} {code}".rstrip()


def qty_text(e: Entry, style: str) -> str | None:
    if e.weight is not None:
        w = f"{e.weight:.2f}"
        u = m(e.unit_cents)
        return {
            "walmart": f"  {w} lb @ 1 lb /{u}",
            "target": f"  {w} lb @ ${u}/lb",
            "shoprite": f"  {w} lb @ {u.lstrip('0') if e.unit_cents < 100 else u} /lb",
            "costco": f"  {w} lb @ {u} /lb",
            "traderjoes": f"  {w} lb @ {u} /lb",
            "hmart": f"  {w} LB @ {u} /LB",
        }[style]
    if e.qty > 1:
        u = m(e.unit_cents)
        return {
            "walmart": f"  {e.qty} AT 1 FOR {u}",
            "target": f"  {e.qty} @ ${u} ea",
            "shoprite": f"  {e.qty} @ {u}",
            "costco": f"  {e.qty} @ {u}",
            "traderjoes": f"  {e.qty} @ {u}",
            "hmart": f"  {e.qty} @ {u}",
        }[style]
    return None


QTY_ABOVE = {"shoprite", "costco"}


def render_thermal(r: Receipt, rng: random.Random) -> tuple[str, list[str], int]:
    p = r.profile
    width = {
        "walmart": 46,
        "target": 44,
        "costco": 44,
        "shoprite": 42,
        "traderjoes": 40,
        "hmart": 40,
    }[p]
    out: list[str] = []
    big = {"walmart": "Walmart", "target": "TARGET", "costco": "COSTCO", "shoprite": "ShopRite",
           "traderjoes": "TRADER JOE'S", "hmart": "H MART"}[p]  # fmt: skip
    y, mo, d = r.date.split("-")
    sub = {
        "walmart": ["Save money. Live better.", "( 201 ) 555 - 0134", "MGR:DANA LOPEZ", "400 PARK AVE",
                    "HOBOKEN NJ 07030", "ST# 02314 OP# 009043 TE# 43 TR# 07718"],
        "target": ["Hoboken - 1 Washington St", "201-555-0188", f"{mo}/{d}/{y} 06:41 PM"],
        "costco": ["WHOLESALE", "Teterboro #1062", "1 Industrial Ave", "Teterboro, NJ 07608",
                   "Member 111987654321"],
        "shoprite": ["ShopRite of Hoboken", "900 Madison St", "Hoboken NJ 07030", "(201) 555-0110"],
        "traderjoes": ["1 Hudson Pl", "Hoboken, NJ 07030", "Store #615 - (201) 555-0172",
                       "OPEN 8:00AM TO 9:00PM DAILY"],
        "hmart": ["Edison", "1761 Route 27", "Edison, NJ 08817", "TEL (732) 555-0199", f"{y}-{mo}-{d} 13:10"],
    }[p]  # fmt: skip
    out.append("")
    dept = None
    for e in r.entries:
        if p in ("target", "shoprite"):
            nd = dept_of(e.product)
            if nd != dept:
                out.append(nd)
                dept = nd
        name = printed_caps(r, e)
        q = qty_text(e, p)
        if q and p in QTY_ABOVE:
            out.append(q)
        tm = p in ("shoprite", "costco", "walmart")
        if p == "walmart":
            code = e.code or f"{rng.randrange(10**11, 10**12):012d}"
            e.code = code
            left = f"{name} {code} {'KF' if e.weight else 'F'}"
            tail = money_tail(e.total, "X" if e.taxable else "N", trailing_minus=tm)
        elif p == "target":
            code = (
                e.code
                or f"{rng.randrange(1, 300):03d}-{rng.randrange(1, 99):02d}-{rng.randrange(1, 9999):04d}"
            )
            e.code = code
            left = f"{code} {'RETURN ' if e.refund else ''}{name}"
            tail = f"{'T' if e.taxable else 'NF'} {m(e.total, dollar=True)}"
        elif p == "costco":
            code = e.code or f"{rng.randrange(100000, 1999999)}"
            e.code = code
            left = (
                f"{'' if e.taxable else 'E'} {code} {name}".strip()
                if not e.taxable
                else f"  {code} {name}"
            )
            tail = money_tail(e.total, "A" if e.taxable else "", trailing_minus=True)
        elif p == "shoprite":
            left = f"{'RETURN ' if e.refund else ''}{name}"
            tail = money_tail(e.total, "T" if e.taxable else "F", trailing_minus=True)
        elif p == "traderjoes":
            left = f"{'RTN ' if e.refund else ''}{name}"
            tail = m(e.total)
        else:  # hmart
            left = name
            tail = m(e.total)
        out.extend(row(left, tail, width))
        if q and p not in QTY_ABOVE:
            out.append(q)
        if e.discount:
            label, amt = e.discount
            if p == "costco":
                out.extend(
                    row(
                        f"  {rng.randrange(300000, 399999)} /{e.code}",
                        f"{m(amt)}-",
                        width,
                    )
                )
            elif p == "target":
                out.extend(row(f"  {label}", f"-{m(amt, dollar=True)}", width))
            elif p in ("shoprite", "walmart"):
                out.extend(row(f"  {label}", f"{m(amt)}-", width))
            else:
                out.extend(row(f"  {label}", f"-{m(amt)}", width))
        if e.deposit:
            out.extend(row("  BOTTLE DEPOSIT", m(e.deposit), width))
    for label, amt in r.order_discounts:
        out.extend(row(label, f"{m(amt)}-", width))
    for kind, label, amt in r.fees:
        if "bag" in label.lower():
            out.extend(row(label.upper(), m(amt), width))
    out.append("")
    total = r.total + r.mismatch
    st = subtotal_printed(r)
    if p == "walmart":
        out += row("SUBTOTAL", m(st), width)
        out += row("TAX 1  6.625 %", m(r.tax), width)
        out += row("TOTAL", m(total), width)
        out += row("VISA TEND", m(total), width)
        out += ["VISA  **** **** **** 4412"]
        out += row("CHANGE DUE", "0.00", width)
        out += [
            "",
            f"# ITEMS SOLD {len(r.entries)}",
            "TC# 4417 8812 0034 5512 9023",
            f"{mo}/{d}/{y[2:]}  14:32:11",
        ]
    elif p == "target":
        out += row("SUBTOTAL", m(st, dollar=True), width)
        taxable = sum(e.net for e in r.entries if e.taxable)
        out += row(
            f"T = NJ TAX 6.6250 on {m(taxable, dollar=True)}",
            m(r.tax, dollar=True),
            width,
        )
        out += row("TOTAL", m(total, dollar=True), width)
        out += row("*4412 VISA CHARGE", m(total, dollar=True), width)
        out += ["AID: A0000000031010", "", "Target Circle: 1% earned today"]
    elif p == "costco":
        out += row("SUBTOTAL", m(st), width)
        out += row("TAX", m(r.tax), width)
        out += row("**** TOTAL", m(total), width)
        out += [
            "XXXXXXXXXXXX4412 CHIP Read",
            "AID: A0000000031010",
            "Seq# 12345 App#: 03312B",
            "Visa Resp: APPROVED",
        ]
        out += row("AMOUNT:", m(total, dollar=True), width)
        out += [f"{mo}/{d}/{y} 15:22 1062 12 412 77"]
        out += row("Visa", m(total), width)
        out += row("CHANGE", "0.00", width)
        out += row("A 6.625% TAX", m(r.tax), width)
        out += row("TOTAL TAX", m(r.tax), width)
        out += [f"TOTAL NUMBER OF ITEMS SOLD = {len(r.entries)}"]
        saved = sum(e.discount[1] for e in r.entries if e.discount)
        if saved:
            out += row("INSTANT SAVINGS", m(saved, dollar=True), width)
        out += ["OP#: 412 Name: Kim", "Thank You!", "Please Come Again"]
    elif p == "shoprite":
        out += row("SUBTOTAL", m(st), width)
        out += row("NJ SALES TAX", m(r.tax), width)
        out += row("**** BALANCE DUE", m(total), width)
        out += row("VISA", m(total), width)
        out += row("CHANGE", ".00", width)
        saved = sum(e.discount[1] for e in r.entries if e.discount) + sum(
            c for _, c in r.order_discounts
        )
        if saved:
            out += ["", center("PRICE PLUS CLUB CARD", width)]
            out += row("TOTAL SAVINGS", m(saved), width)
        out += ["", f"{mo}/{d}/{y} 14:31  0110 05 0042 2231"]
    elif p == "traderjoes":
        out += row("SUBTOTAL", f"${m(st)}", width)
        if r.tax:
            out += row("TAX", f"${m(r.tax)}", width)
        out += row("Balance to pay", f"${m(total)}", width)
        out += row("Visa", f"${m(total)}", width)
        out += [
            "",
            f"Items in Transaction: {len(r.entries)}",
            f"{mo}-{d}-{y} 5:47PM",
            "THANK YOU FOR SHOPPING AT",
            "TRADER JOE'S",
        ]
    else:  # hmart
        out += row("SUBTOTAL", m(st), width)
        out += row("TAX", m(r.tax), width)
        out += row("TOTAL", m(total), width)
        out += row("MASTERCARD", m(total), width)
        out += ["CARD # XXXXXXXXXXXX7731", "", "THANK YOU! COME AGAIN"]
    return thermal_html(r.rid, out, width, big=big, sub=sub), out, width


def dept_of(p: Product) -> str:
    return {"produce": "PRODUCE", "dairy": "DAIRY", "eggs": "DAIRY", "meat_fish": "MEAT", "frozen": "FROZEN",
            "household": "HOUSEHOLD"}.get(p.category, "GROCERY")  # fmt: skip


# --- profiles: e-receipts ---------------------------------------------------------------------------

ERECEIPT_CSS = """
html,body{margin:0;background:#fff}
body{width:720px;padding:28px 36px;box-sizing:border-box;color:#222;font-family:Arial,Helvetica,sans-serif;
font-size:15px;line-height:21px}
.logo{font-size:30px;font-weight:700;line-height:38px;margin-bottom:6px}
.r{display:flex;justify-content:space-between;break-inside:avoid}
.r .n{white-space:pre}
.sub{color:#555;font-size:13.5px;padding-left:18px}
.hd{font-weight:700;border-bottom:2px solid #222;margin-top:12px;padding-bottom:3px}
.sum{margin-left:330px}
.tot{font-weight:700;font-size:17px;border-top:2px solid #222;padding-top:4px;margin-top:4px}
hr{border:none;border-top:1px solid #bbb;margin:10px 0}
.q{display:inline-block;width:72px}
.ind{padding-left:72px}
"""


def wrap(text: str, n: int) -> list[str]:
    out, cur = [], ""
    for w in text.split(" "):
        if cur and len(cur) + 1 + len(w) > n:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    out.append(cur)
    return out


def e(s: str) -> str:
    return html.escape(s)


def render_instacart(r: Receipt) -> str:
    y, mo, d = (int(x) for x in r.date.split("-"))
    months = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    import datetime as _dt

    wd = _dt.date(y, mo, d).strftime("%a")
    parts = [
        '<div class="logo">instacart</div>',
        "<div>Your Wegmans order receipt</div>",
        f"<div>Delivered: {wd}, {months[mo - 1]} {d}, {y}</div>",
        "<div>Order # 1857-2290-4471</div>",
        '<div class="r hd"><span>Items</span><span>Price</span></div>',
    ]
    for en in r.entries:
        name_lines = wrap(truth_name(r, en), 52)
        for ln in name_lines[:-1]:
            parts.append(f'<div class="r"><span class="n">{e(ln)}</span></div>')
        parts.append(
            f'<div class="r"><span class="n">{e(name_lines[-1])}</span><span>{m(en.total, True)}</span></div>'
        )
        if en.weight is not None:
            parts.append(
                f'<div class="sub">{en.weight:.2f} lb × {m(en.unit_cents, True)}/lb</div>'
            )
        elif en.qty > 1:
            parts.append(f'<div class="sub">{en.qty} × {m(en.unit_cents, True)}</div>')
        if en.discount:
            parts.append(
                f'<div class="r sub"><span>{e(en.discount[0])}</span><span>-{m(en.discount[1], True)}</span></div>'
            )
        if en.deposit:
            parts.append(
                f'<div class="r sub"><span>Bottle deposit</span><span>{m(en.deposit, True)}</span></div>'
            )
    parts.append("<hr>")
    summ = [("Items subtotal", m(subtotal_printed(r), True))]
    for kind, label, amt in r.fees:
        summ.append((label, m(amt, True)))
    for label, amt in r.order_discounts:
        summ.append((label, f"-{m(amt, True)}"))
    summ.append(("Tax", m(r.tax, True)))
    # tips print after tax on Instacart
    summ = [s for s in summ if not s[0].lower().endswith("tip")] + [
        s for s in summ if s[0].lower().endswith("tip")
    ]
    for label, val in summ:
        parts.append(
            f'<div class="r sum"><span>{e(label)}</span><span>{val}</span></div>'
        )
    parts.append(
        f'<div class="r sum tot"><span>Total</span><span>{m(r.total + r.mismatch, True)}</span></div>'
    )
    parts.append(
        "<hr><div>Charged to Visa ending in 4412</div><div>Questions? Visit instacart.com/help</div>"
    )
    return f'<!doctype html><html><head><meta charset="utf-8"><style>{ERECEIPT_CSS}</style></head><body>{NL}{NL.join(parts)}{NL}</body></html>'


def render_amazon(r: Receipt) -> str:
    y, mo, d = (int(x) for x in r.date.split("-"))
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
              "November", "December"]  # fmt: skip
    parts = [
        '<div class="logo">amazon</div>',
        "<div>Whole Foods Market order</div>",
        f"<div>Order placed: {months[mo - 1]} {d}, {y}</div>",
        "<div>Order # 112-4471932-5518206</div>",
        '<div class="r hd"><span><span class="q">Qty</span>Item</span><span>Price</span></div>',
    ]
    for en in r.entries:
        name_lines = wrap(truth_name(r, en), 50)
        q = f"{en.weight:.2f} lb" if en.weight is not None else str(en.qty)
        for i, ln in enumerate(name_lines):
            qcell = (
                f'<span class="q">{e(q)}</span>'
                if i == 0
                else '<span class="q"></span>'
            )
            price = (
                f"<span>{m(en.total, True)}</span>" if i == len(name_lines) - 1 else ""
            )
            parts.append(
                f'<div class="r"><span class="n">{qcell}{e(ln)}</span>{price}</div>'
            )
        if en.qty > 1 or en.weight is not None:
            unit = (
                f"{m(en.unit_cents, True)}/lb"
                if en.weight is not None
                else f"{m(en.unit_cents, True)} each"
            )
            parts.append(f'<div class="sub ind">{unit}</div>')
        if en.discount:
            parts.append(
                f'<div class="r sub ind"><span>{e(en.discount[0])}</span><span>-{m(en.discount[1], True)}</span></div>'
            )
    parts.append("<hr>")
    summ = [("Item(s) Subtotal:", m(subtotal_printed(r), True))]
    for kind, label, amt in r.fees:
        summ.append((f"{label}:", m(amt, True)))
    for label, amt in r.order_discounts:
        summ.append((f"{label}:", f"-{m(amt, True)}"))
    summ.append(("Estimated tax to be collected:", m(r.tax, True)))
    for label, val in summ:
        parts.append(
            f'<div class="r sum"><span>{e(label)}</span><span>{val}</span></div>'
        )
    parts.append(
        f'<div class="r sum tot"><span>Grand Total:</span><span>{m(r.total + r.mismatch, True)}</span></div>'
    )
    parts.append(
        "<hr><div>Payment method: Visa ending in 4412</div><div>Thank you for shopping with us.</div>"
    )
    return f'<!doctype html><html><head><meta charset="utf-8"><style>{ERECEIPT_CSS}</style></head><body>{NL}{NL.join(parts)}{NL}</body></html>'


# --- receipt specs -------------------------------------------------------------------------------------


def E(key: str, **kw: Any) -> Entry:
    return Entry(BY_KEY[key], **kw)


def w(rng: random.Random, lo: float, hi: float) -> Decimal:
    return Decimal(rng.randrange(int(lo * 100), int(hi * 100))) / 100


def receipts() -> list[Receipt]:
    rng = random.Random(SEED)
    R = Receipt
    rs: list[Receipt] = []
    # r01 ShopRite: the Scan.dc.html example (weighted bananas and thighs above the line, Price Plus savings).
    rs.append(R("r01", "shoprite", "2026-10-02", [
        E("milk"), E("eggs"), E("rice"), E("yogurt", discount=("PRICE PLUS SAVINGS", 100)),
        E("bananas", weight=Decimal("2.10")), E("towels"), E("dishsoap"), E("thighs", weight=Decimal("3.00")),
        E("oatmilk"), E("spinach"), E("bars"), E("coffee"),
    ], fees=[("fee", "Bag fee", 10)]))  # fmt: skip
    # r02 Walmart: GV store brand, weighted and multi-quantity lines below the item, tax flags.
    rs.append(R("r02", "walmart", "2026-09-28", [
        E("milk", brand=True, unit=348), E("eggs", brand=True, unit=412), E("apples", weight=w(rng, 1.5, 3.5)),
        E("beans", brand=True, qty=3, unit=98), E("pasta", brand=True, qty=2, unit=125), E("cheddar", brand=True, unit=248),
        E("tp", unit=1597), E("bananas", weight=w(rng, 1.8, 3.2), unit=50), E("pb", brand=True, unit=228),
        E("trashbags", unit=897), E("seltzer", unit=448), E("cereal", brand=True, unit=298),
    ]))  # fmt: skip
    # r03 Target: DPCI codes, department headers, Circle discounts, qty and weight lines.
    rs.append(R("r03", "target", "2026-09-30", [
        E("milk", brand=True, unit=399), E("yogurt", brand=True, discount=("Target Circle 10% off", 68)),
        E("strawberries", brand=True), E("grapes", weight=w(rng, 1.2, 2.6)), E("tortillas", brand=True, qty=2, unit=219),
        E("chips", brand=True, discount=("Target Circle 20% off", 86)), E("icecream", brand=True), E("dishsoap"),
        E("sponges"), E("oats", brand=True),
    ]))  # fmt: skip
    # r04 Costco: item numbers, instant-savings coupons that reference the item number, qty above.
    rs.append(R("r04", "costco", "2026-09-26", [
        E("eggs", brand=True, unit=799),
        E("almonds", brand=True, unit=1349, discount=("", 300)), E("towels", brand=True, unit=2199, discount=("", 400)),
        E("oliveoil", brand=True, unit=1699), E("blueberries", qty=2, unit=599), E("salmon", unit=3499),
        E("coffee", brand=True, unit=1899, discount=("", 350)), E("detergent", brand=True, unit=1999),
        E("dumplings", unit=1349), E("bread", unit=599),
    ]))  # fmt: skip
    # r05 Instacart: weighted + qty sub-lines, coupon, deposit, bag/delivery/service fees and a tip.
    rs.append(R("r05", "instacart", "2026-10-01", [
        E("bananas", weight=w(rng, 1.2, 2.4)), E("yogurt", discount=("Coupon: Save $1.00", 100)),
        E("seltzer", qty=2, deposit=120), E("spinach"), E("thighs", weight=w(rng, 1.5, 2.8)),
        E("pasta", qty=3), E("marinara"), E("detergent"), E("cheddar"),
        E("coffee", long_name="Stumptown Hair Bender Whole Bean Coffee, Medium Roast, Small Batch, 12 oz Bag"),
    ], fees=[("fee", "Checkout bag fee", 30), ("fee", "Delivery fee", 399), ("fee", "Service fee", 215),
             ("tip", "Shopper tip", 600)]))  # fmt: skip
    # r06 Amazon / Whole Foods: 2 pages, leading qty column, long wrapped names, Prime savings.
    keys6 = ["milk", "eggs", "yogurt", "spinach", "strawberries", "blueberries", "avocado", "limes", "garlic",
             "cilantro", "carrots", "butter", "mozz", "oatmilk", "oj", "tofu", "hummus", "pasta", "oats", "bread",
             "pb", "oliveoil", "beans", "tomcan", "coffee", "tea", "chips", "almonds", "cookies", "icecream",
             "peas", "towels"]  # fmt: skip
    ents6 = []
    for i, k in enumerate(keys6):
        en = E(k, brand=i % 3 == 0, qty=2 if i % 7 == 3 else 1)
        if i % 8 == 5:
            en.discount = ("Prime member savings", max(BY_KEY[k].price // 10, 25))
        ents6.append(en)
    rs.append(
        R(
            "r06",
            "amazon",
            "2026-09-24",
            ents6,
            fees=[("fee", "Service fee", 995), ("tip", "Driver tip", 800)],
        )
    )
    # r07 Trader Joe's: qty lines below, a returned item (negative), a $0.00 item.
    rs.append(R("r07", "traderjoes", "2026-09-27", [
        E("bananas", qty=6, unit=29), E("spinach", unit=249), E("hummus", unit=299),
        E("orangechkn", refund=True), E("cilantro", zero=True), E("almonds", unit=599), E("oatmilk", unit=349),
        E("eggs", unit=479), E("bread", unit=299), E("pizza", unit=549), E("cookies", unit=399, qty=2),
    ]))  # fmt: skip
    # r08 H Mart: long names that wrap, weighted meat below the line, member discount, bag fee.
    rs.append(R("r08", "hmart", "2026-09-29", [
        E("kimchi", long_name="Jongga Mat Kimchi Napa Cabbage Kimchi 32 oz"),
        E("porkbelly", weight=w(rng, 1.4, 2.4), discount=("MEMBER DISCOUNT", 100)),
        E("ramen", long_name="Nongshim Shin Ramyun Noodle Soup Spicy 4 pk"),
        E("dumplings", long_name="CJ Bibigo Pork & Vegetable Mini Wontons 24 oz"),
        E("tofu", long_name="Pulmuone Organic Firm Tofu 14 oz"), E("gochujang"),
        E("onions", weight=w(rng, 1.5, 3.0)), E("tea", qty=2, unit=399),
        E("rice", long_name="CJ Hetbahn Cooked White Rice 12 pk", unit=1599), E("sponges", unit=199),
    ], fees=[("fee", "Bag fee", 10)]))  # fmt: skip
    # r09 Walmart: the printed total is $1.00 higher than the lines add up to (deliberate mismatch).
    rs.append(R("r09", "walmart", "2026-10-03", [
        E("milk", brand=True, unit=348), E("bread", brand=True, unit=228), E("bananas", weight=w(rng, 2.0, 3.4), unit=50),
        E("beef", weight=w(rng, 1.0, 2.2), unit=549), E("tomcan", brand=True, qty=4, unit=112), E("rice", brand=True, unit=1192),
        E("cola", qty=2, unit=198), E("pizza", unit=598), E("peas", brand=True, qty=3, unit=118),
        E("tp", unit=2297), E("sponges", unit=372), E("butter", brand=True, unit=398), E("oj", unit=328),
        E("cookies", unit=348), E("onions", weight=w(rng, 1.0, 2.5), unit=118),
    ], mismatch=100))  # fmt: skip
    # r10 Costco: a long receipt split over 3 pages, many coupons, a $0.00 line.
    keys10 = ["eggs", "almonds", "towels", "oliveoil", "blueberries", "salmon", "coffee", "detergent", "dumplings",
              "bread", "milk", "butter", "cheddar", "mozz", "oatmilk", "oj", "thighs", "beef", "rice", "pasta",
              "ramen", "oats", "cereal", "pb", "marinara", "beans", "tomcan", "tea", "chips", "bars", "cookies",
              "seltzer", "cola", "icecream", "pizza", "peas", "tp", "trashbags", "sponges", "dishsoap"]  # fmt: skip
    ents10 = []
    for i, k in enumerate(keys10):
        pr = BY_KEY[k]
        en = E(k, brand=i % 2 == 0, unit=pr.price * 2 + (99 - pr.price * 2 % 100))
        if i % 6 == 2:
            en.discount = (
                "",
                min(rng.choice([200, 300, 350, 400, 500]), en.unit_cents // 2),
            )  # coupons never exceed the item
        if i % 9 == 4:
            en.qty = 2
        ents10.append(en)
    ents10[21].zero = True
    rs.append(R("r10", "costco", "2026-09-21", ents10))
    # r11 ShopRite: bottle deposits, a return, an order-level store coupon, qty/weight above the line.
    rs.append(R("r11", "shoprite", "2026-10-04", [
        E("cola", qty=2, deposit=20), E("seltzer", deposit=60), E("apples", weight=w(rng, 1.0, 2.5)),
        E("milk", discount=("PRICE PLUS SAVINGS", 50)), E("bread", refund=True), E("pasta", qty=4, unit=99),
        E("marinara"), E("beef", weight=w(rng, 1.1, 1.9)), E("chips"), E("trashbags"),
    ], order_discounts=[("STORE COUPON $5 OFF ORDER", 500)], fees=[("fee", "Bag fee", 20)]))  # fmt: skip
    # r12 Instacart: 2 pages, order-level promotion, $0.00 item, fees and tip.
    keys12 = ["milk", "eggs", "yogurt", "spinach", "strawberries", "avocado", "limes", "carrots", "butter",
              "oatmilk", "oj", "thighs", "beef", "tofu", "hummus", "rice", "pasta", "ramen", "cereal", "bread",
              "tortillas", "beans", "coffee", "chips", "bars", "seltzer", "icecream", "dumplings", "towels", "tp",
              "dishsoap"]  # fmt: skip
    ents12 = []
    for i, k in enumerate(keys12):
        en = E(k)
        if BY_KEY[k].per_lb:
            en.weight = w(rng, 0.8, 2.6)
        elif i % 6 == 1:
            en.qty = 2
        if i % 10 == 4:
            en.discount = ("Coupon: Save $0.75", 75)
        ents12.append(en)
    ents12[6].zero = True
    ents12[6].long_name = "Limes (free replacement)"
    rs.append(R("r12", "instacart", "2026-09-19", ents12,
                fees=[("fee", "Delivery fee", 0 + 499), ("fee", "Service fee", 412), ("tip", "Shopper tip", 1000)],
                order_discounts=[("Promotion (WELCOME5)", 500)]))  # fmt: skip
    # r13 Target: a return, a $0.00 item, Circle discounts, multi-quantity.
    rs.append(R("r13", "target", "2026-10-05", [
        E("cereal", brand=True), E("milk", brand=True, unit=399), E("cookies", brand=True, refund=True),
        E("beans", brand=True, qty=4, unit=89), E("peas", brand=True, zero=True),
        E("pizza", discount=("Target Circle 15% off", 105)), E("trashbags"), E("tea"), E("butter", brand=True),
    ]))  # fmt: skip
    # r14 Trader Joe's: short and simple, weighted and qty lines below.
    rs.append(R("r14", "traderjoes", "2026-10-06", [
        E("tomatoes", weight=w(rng, 1.0, 2.0), unit=249), E("carrots", unit=169), E("hummus", unit=349),
        E("mozz", unit=399), E("pasta", qty=3, unit=99), E("marinara", unit=299), E("coffee", unit=899),
        E("cola", unit=199),
    ]))  # fmt: skip
    # r15 Amazon / Whole Foods: one page, delivery/service fees, tip; printed total $0.50 short (mismatch).
    rs.append(R("r15", "amazon", "2026-10-07", [
        E("bananas", weight=w(rng, 1.4, 2.4), unit=49), E("salmon", weight=w(rng, 0.8, 1.4)),
        E("milk", brand=True), E("eggs", brand=True, discount=("Prime member savings", 100)),
        E("spinach"), E("bread", brand=True), E("oliveoil", brand=True, qty=2),
        E("detergent"),
    ], fees=[("fee", "Delivery fee", 995), ("fee", "Service fee", 300), ("tip", "Driver tip", 500)], mismatch=-50))  # fmt: skip
    return rs


# image fixtures: (id, source receipt, output format, noise preset, split into photos)
IMAGES: list[dict[str, Any]] = [
    {"id": "i01", "src": "r01", "fmt": "jpg", "noise": {"rot": -3.5, "blur": 0.8, "shadow": 0.35, "fade": 0.0, "warp": 2.0, "q": 72}},
    {"id": "i02", "src": "r02", "fmt": "jpg", "noise": {"rot": 6.5, "blur": 0.6, "shadow": 0.2, "fade": 0.25, "warp": 3.0, "persp": 0.05, "q": 60}},
    {"id": "i03", "src": "r03", "fmt": "png", "noise": {"rot": -6.8, "blur": 1.0, "shadow": 0.25, "fade": 0.1, "warp": 1.5, "q": 0}},
    {"id": "i04", "src": "r04", "fmt": "jpg", "noise": {"rot": 2.0, "blur": 1.6, "shadow": 0.45, "fade": 0.45, "warp": 4.0, "persp": 0.07, "q": 40}},
    {"id": "i05", "src": "r05", "fmt": "pdf", "noise": {"rot": 1.2, "blur": 0.7, "shadow": 0.15, "fade": 0.0, "warp": 0.0, "q": 70}},
    {"id": "i06", "src": "r06", "fmt": "pdf", "pages": 2, "noise": {"rot": -1.5, "blur": 0.8, "shadow": 0.1, "fade": 0.0, "warp": 0.0, "q": 70}},
    {"id": "i07", "src": "r07", "fmt": "jpg", "noise": {"rot": 4.0, "blur": 0.9, "shadow": 0.3, "fade": 0.6, "warp": 2.5, "q": 65}},
    {"id": "i08", "src": "r08", "fmt": "png", "noise": {"rot": -2.5, "blur": 0.7, "shadow": 0.4, "fade": 0.2, "warp": 5.0, "persp": 0.06, "q": 0}},
    {"id": "i09", "src": "r09", "fmt": "jpg", "noise": {"rot": 7.0, "blur": 1.1, "shadow": 0.3, "fade": 0.15, "warp": 2.0, "q": 35}},
    {"id": "i10", "src": "r10", "fmt": "jpg", "photos": 3, "noise": {"rot": -4.5, "blur": 0.8, "shadow": 0.3, "fade": 0.2, "warp": 3.0, "q": 62}},
    {"id": "i11", "src": "r11", "fmt": "pdf", "noise": {"rot": -5.5, "blur": 1.0, "shadow": 0.35, "fade": 0.3, "warp": 3.5, "persp": 0.05, "q": 60}},
    {"id": "i12", "src": "r12", "fmt": "jpg", "photos": 2, "noise": {"rot": 3.0, "blur": 0.7, "shadow": 0.2, "fade": 0.0, "warp": 1.0, "q": 68}},
    {"id": "i13", "src": "r13", "fmt": "jpg", "noise": {"rot": -7.0, "blur": 1.5, "shadow": 0.5, "fade": 0.35, "warp": 4.5, "persp": 0.08, "q": 45}},
    {"id": "i14", "src": "r14", "fmt": "png", "noise": {"rot": 5.0, "blur": 1.0, "shadow": 0.25, "fade": 0.5, "warp": 2.0, "q": 0}},
    {"id": "i15", "src": "r15", "fmt": "pdf", "noise": {"rot": 0.8, "blur": 0.9, "shadow": 0.2, "fade": 0.15, "warp": 1.0, "q": 66}},
]  # fmt: skip


# --- build ------------------------------------------------------------------------------------------------


def build_html(r: Receipt, rng: random.Random) -> tuple[str, list[str] | None, int]:
    if r.profile in THERMAL:
        doc, lines, width = render_thermal(r, rng)
        return doc, lines, width * 9 + 40
    if r.profile == "instacart":
        return render_instacart(r), None, 720
    return render_amazon(r), None, 720


def split_html(doc: str, parts: int, overlap: int) -> list[str]:
    """Split a rendered receipt into `parts` photos at line boundaries, repeating `overlap` lines."""
    head, _, rest = doc.partition("<body>")
    body, _, tail = rest.partition("</body>")
    # Thermal bodies are <div class="l"> rows after a header block; e-receipts are rows of divs.
    chunks = body.split("\n")
    n = len(chunks)
    size = n // parts
    out = []
    for i in range(parts):
        start = max(i * size - (overlap if i else 0), 0)
        end = n if i == parts - 1 else (i + 1) * size
        out.append(f"{head}<body>{chr(10).join(chunks[start:end])}</body>{tail}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--skip-render",
        action="store_true",
        help="reuse PNG/PDF renders in .cache/receipts-build",
    )
    args = ap.parse_args()
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH", str(REPO / ".cache" / "ms-playwright")
    )
    BUILD.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    rs = receipts()
    manifest: dict[str, Any] = {"seed": SEED, "fixtures": []}
    jobs: list[dict[str, Any]] = []
    truths: dict[str, ExtractedBill] = {}
    docs: dict[str, tuple[str, int]] = {}
    for r in rs:
        rng = random.Random(f"{SEED}-{r.rid}")
        doc, _, width = build_html(r, rng)
        docs[r.rid] = (doc, width)
        html_path = BUILD / f"{r.rid}.html"
        html_path.write_text(doc, encoding="utf-8")
        truths[r.rid] = truth_bill(r)
        job: dict[str, Any] = {
            "html": str(html_path),
            "pdf": str(OUT / f"{r.rid}.pdf"),
            "width": width,
            "scale": 1,
        }
        if r.profile in ("instacart", "amazon"):
            job["pdfFormat"] = "Letter"
            job["footer"] = True
        elif r.rid == "r10":
            job["pageHeight"] = 900
        jobs.append(job)
        multi = truths[r.rid]
        manifest["fixtures"].append({
            "id": r.rid, "kind": "text_pdf", "profile": r.profile, "files": [f"{r.rid}.pdf"],
            "expects_mismatch": multi.mismatch_cents is not None, "notes": r.notes,
        })  # fmt: skip
    for spec in IMAGES:
        src = spec["src"]
        doc, width = docs[src]
        photos = spec.get("photos") or spec.get("pages") or 1
        pieces = (
            [doc]
            if photos == 1
            else split_html(doc, photos, 3 if spec.get("photos") else 0)
        )
        for k, piece in enumerate(pieces, start=1):
            hp = BUILD / f"{spec['id']}_{k}.html"
            hp.write_text(piece, encoding="utf-8")
            jobs.append(
                {
                    "html": str(hp),
                    "png": str(BUILD / f"{spec['id']}_{k}.png"),
                    "width": width,
                    "scale": 2.2,
                }
            )
    if not args.skip_render:
        jobs_path = BUILD / "jobs.json"
        jobs_path.write_text(json.dumps(jobs, indent=1), encoding="utf-8")
        node = shutil.which("node") or "node"
        subprocess.run([node, str(HERE / "render.mjs"), str(jobs_path)], check=True)
    from noise import apply_noise, save_image

    for spec in IMAGES:
        src = spec["src"]
        photos = spec.get("photos") or spec.get("pages") or 1
        rng = random.Random(f"{SEED}-{spec['id']}")
        imgs = [
            apply_noise(BUILD / f"{spec['id']}_{k}.png", spec["noise"], rng)
            for k in range(1, photos + 1)
        ]
        files = save_image(
            imgs, OUT, spec["id"], spec["fmt"], spec["noise"].get("q", 70)
        )
        truth = truths[src].model_copy(deep=True)
        truth.source = "ocr"
        truths[spec["id"]] = truth
        manifest["fixtures"].append({
            "id": spec["id"], "kind": "scanned_pdf" if spec["fmt"] == "pdf" else "image", "source": src,
            "profile": next(x for x in rs if x.rid == src).profile, "files": files,
            "multi_photo": bool(spec.get("photos")), "noise": spec["noise"],
            "expects_mismatch": truth.mismatch_cents is not None,
        })  # fmt: skip
    for fid, truth in truths.items():
        (OUT / f"{fid}.truth.json").write_text(
            json.dumps(truth.model_dump(mode="json"), indent=1, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, indent=1) + "\n", encoding="utf-8"
    )
    total = sum(p.stat().st_size for p in OUT.iterdir() if p.is_file())
    print(f"wrote {len(manifest['fixtures'])} fixtures, {total / 1e6:.1f} MB in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
