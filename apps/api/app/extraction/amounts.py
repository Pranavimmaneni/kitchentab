"""Money and quantity parsing for receipts. Integer cents only: no floats anywhere (PRD §6).

`parse_cents("4.29") == 429`, `parse_cents("1,234.50-") == -123450`. OCR repairs (O→0, l→1, S→5,
a missing decimal point) live in `repair_amount`, which also returns how sure it is.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# A strict money token: optional sign/$, digits with optional thousands commas, exactly 2 decimals,
# optional trailing minus or parentheses.
_MONEY = re.compile(
    r"^(?P<neg1>[-–])?\(?\$?\s?(?P<neg2>[-–])?(?P<int>\d{1,3}(?:,\d{3})+|\d+)?\.(?P<dec>\d{2})\)?(?P<neg3>-)?$"
)

# Characters OCR commonly confuses inside numbers.
_DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "l": "1", "I": "1", "i": "1", "|": "1", "!": "1",
                            "L": "1", "S": "5", "s": "5", "Z": "2", "z": "2", "B": "8", "g": "9", "G": "6", "b": "6",
                            "T": "7", "A": "4"})  # fmt: skip


def parse_cents(token: str) -> int | None:
    """Parse a clean money token into integer cents, or None."""
    t = token.strip().replace(" ", "")
    m = _MONEY.match(t)
    if not m:
        return None
    whole = int((m.group("int") or "0").replace(",", ""))
    cents = whole * 100 + int(m.group("dec"))
    negative = bool(m.group("neg1") or m.group("neg2") or m.group("neg3")) or (t.startswith("(") and t.endswith(")"))
    return -cents if negative else cents


def cents_to_str(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}{c // 100}.{c % 100:02d}"


def repair_amount(token: str, *, allow_missing_point: bool = False) -> tuple[int, int] | None:
    """Parse a money token that may contain OCR noise. Returns (cents, certainty_x1000) or None.

    Certainty (thousandths) is 1000 for a clean token, lower when characters had to be fixed, and lowest when the
    decimal point was missing and had to be inferred (only when `allow_missing_point`).
    """
    t = token.strip()
    if not t:
        return None
    exact = parse_cents(t)
    if exact is not None:
        return exact, 1000
    certainty = 1000
    # Strip stray characters around the token (quotes, underscores, trailing tax letters glued on).
    core = t.strip("'\"`_~*.:;,")
    neg = False
    if (
        core.endswith("-")
        or core.startswith("-")
        or core.startswith("–")
        or (core.startswith("(") and core.endswith(")"))
    ):
        neg = True
    core = core.strip("-–()")
    if core.startswith(("$", "S$")):
        core = core.lstrip("S$")
    elif core[:1] in ("S", "s") and len(core) > 4 and re.search(r"\d", core[1:]):
        # "S4.29": a dollar sign read as S. Only when what follows is already a full amount.
        rest = core[1:].translate(_DIGIT_FIX)
        if re.fullmatch(r"\d+[.,]\d{2}", rest):
            core = core[1:]
            certainty = min(certainty, 850)
    fixed = core.translate(_DIGIT_FIX)
    if fixed != core:
        certainty = min(certainty, 850)
    # A comma used as the decimal point ("4,29").
    m = re.fullmatch(r"(\d+)[,](\d{2})", fixed)
    if m:
        fixed = f"{m.group(1)}.{m.group(2)}"
        certainty = min(certainty, 850)
    # Two separators read for one ("4..29", "4.,29") or a space inside ("4. 29").
    fixed = re.sub(r"(\d)[.,]\s?[.,]?(\d{2})$", r"\1.\2", fixed)
    cents = parse_cents(fixed)
    if cents is not None:
        return (-cents if neg else cents), certainty
    if allow_missing_point and re.fullmatch(r"\d{3,5}", fixed):
        val = int(fixed)
        return (-val if neg else val), min(certainty, 600)
    return None


def parse_quantity(token: str) -> Decimal | None:
    """'2', '2.10', '0.5' → Decimal. Rejects anything else."""
    t = token.strip().translate(str.maketrans("OolI", "0011"))
    if not re.fullmatch(r"\d{1,4}(?:\.\d{1,3})?", t):
        return None
    try:
        q = Decimal(t)
    except InvalidOperation:  # pragma: no cover - regex guards this
        return None
    return q if q > 0 else None


def mul_cents(quantity: Decimal, unit_cents: int) -> int:
    """quantity × unit price rounded half-up to a cent (how registers print weighted lines)."""
    return int((quantity * unit_cents).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def normalize_quantity(q: Decimal) -> Decimal:
    """Drop trailing zeros so 2.10 == 2.1 and 2.0 == 2."""
    n = q.normalize()
    # normalize() turns 10 into 1E+1; quantize back to a plain integer exponent when whole.
    if n == n.to_integral_value():
        return n.quantize(Decimal(1))
    return n
