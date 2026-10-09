"""One receipt parser for every input (BUILD_ALL C1 step 3, PRD §9).

Input: positioned lines from a text PDF (`pdf_text.extract_pdf_lines`) or from browser OCR
(`lines.lines_from_ocr`, merged with `lines.merge_parts` for multi-photo receipts).
Output: `ExtractedBill`, the schema the optional LLM plugin also returns.

Generic grammar, top to bottom:
  item line        NAME ...  PRICE [TAX FLAG]          (a trailing price; codes stripped)
  wrapped name     NAME (no price) directly above an aligned priced line
  quantity line    "3 @ $1.29", "2 x 1.29", "2.1 lb @ $0.63/lb", "2 AT 1 FOR 1.48", "QTY 2"
                   (own line above or below its item, or inside the item line; attached where the
                   arithmetic matches, else by the store profile's layout)
  discount line    negative amount or coupon/savings words → `discount_cents` on the item above
                   (Costco "/<item no>" references attach to that item); unclear → order level
  refund line      RETURN/RTN/REFUND → an item with a negative total
  fees             deposits, bag fees, delivery/service fees → fees; tips → kind "tip"
  summary          SUBTOTAL, TAX (summed), TOTAL / BALANCE DUE / Grand Total (parsing stops)
  noise            payment, card, change, savings summaries, page footers, departments

Money is integer cents and confidences are integer thousandths throughout (MONEY-06). The
server never trusts printed arithmetic: `ExtractedBill.recompute()` reports any mismatch.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.extraction.amounts import mul_cents, normalize_quantity, parse_quantity, repair_amount
from app.extraction.classify import classify
from app.extraction.lines import Coord, Line, char_width, price_column
from app.extraction.profiles import (
    ABBREVIATIONS,
    DEPARTMENTS,
    GENERIC,
    TABLE_HEADER_WORDS,
    StoreProfile,
    detect_profile,
)
from app.extraction.schema import (
    FLAG_BELOW,
    MAX_ITEMS,
    ExtractedBill,
    ExtractedFee,
    ExtractedItem,
    ExtractionSource,
    FeeKind,
)

FULL = 1000  # certainty / confidence scale (thousandths)
MIN_OCR_CONF_FOR_ITEM = 400  # an OCR line below this never becomes an item (EDGE-15-01: never invent)
SEAM_CONF = FLAG_BELOW - 1  # kept lines at a multi-photo seam are always flagged

GENERIC_CODES = frozenset({"N", "X", "T", "F", "A", "E", "TF", "NF", "FN", "KF", "TX", "O", "Y", "S", "B", "H"})

# --- keyword patterns (run on an upper-cased, punctuation-light copy of the text) -----------------
SUBTOTAL_RE = re.compile(r"\bSUB ?-?TOTAL\b|\bMERCHANDISE TOTAL\b|\bITEMS? ?S? SUBTOTAL\b")
TOTAL_RE = re.compile(
    r"\b(?:GRAND |ORDER |INVOICE )?TOTAL\b|\bBALANCE(?: DUE| TO PAY)?\b|\bAMOUNT DUE\b|\bTOTAL DUE\b|\bTOTAL CHARGED\b"
)
NOT_TOTAL_RE = re.compile(
    r"SAVING|\bITEMS?\b|NUMBER|\bTAX\b|DISCOUNT|BEFORE|POINTS|REWARD|\bSUB|\bQTY\b|CARD|\bCOUNT\b|\bNET\b|WEIGHT"
)
TAX_RE = re.compile(r"\bTAX\b|\bTAXES\b|\bHST\b|\bGST\b|\bVAT\b|\bSALES TAX\b")
NOT_TAX_RE = re.compile(r"TAXABLE|EXEMPT|BEFORE TAX|PRE-?TAX|NON-?TAX|\bTOTAL TAX\b")
TIP_RE = re.compile(r"\bTIP\b|\bTIPS\b|GRATUITY")
FEE_RE = re.compile(
    r"\bFEES?\b|\bDEPOSITS?\b|\bDEP\b|\bCRV\b|REDEMPTION|\bSHIPPING\b|\bHANDLING\b|\bSURCHARGE\b|\bBAG CHARGE\b|"
    r"\bDELIVERY\b|\bSERVICE CHARGE\b"
)
ORDER_DISCOUNT_RE = re.compile(r"\bORDER\b|PROMOTION|PROMO CODE|\bWELCOME|\$\d+ ?OFF ?\$?\d+|\bOFF \$\d+")
LINE_DISCOUNT_RE = re.compile(
    r"COUPON|\bCPN\b|SAVINGS?\b|DISCOUNT|\bDISC\b|\bMFR\b|PROMO|MEMBER|CIRCLE|PRICE ?PLUS|\bINSTANT\b|"
    r"MARKDOWN|\bBOGO\b|\bPRIME\b|\bOFF\b"
)
POSITIVE_DISCOUNT_RE = re.compile(r"COUPON|SAVINGS|DISCOUNT|PRICE ?PLUS|CIRCLE|\bCPN\b")
INFO_RE = re.compile(
    r"YOU SAVED|TOTAL SAVINGS|INSTANT SAVINGS|SAVED TODAY|ITEMS SOLD|NUMBER OF ITEMS|ITEMS IN TRANSACTION|"
    r"\bPOINTS\b|\bEARNED\b|REWARDS BALANCE|YOUR SAVINGS"
)
PAYMENT_RE = re.compile(
    r"\bVISA\b|MASTERCARD|MASTER CARD|\bAMEX\b|AMERICAN EXPRESS|DISCOVER|\bDEBIT\b|\bCREDIT\b|\bCASH\b|\bCHANGE\b|"
    r"\bTEND\b|\bTENDER|\bCARD\b|\bAUTH|APPROV|\bAID\b|\bAMOUNT\b|\bCHARGE\b|PAYMENT|\bEBT\b|\bPAID\b|\bREF\b|"
    r"ACCOUNT|CHIP READ|CONTACTLESS|\bAPP\b|\bSEQ\b"
)
REFUND_RE = re.compile(r"\bRETURN(?:ED)?\b|\bREFUND(?:ED)?\b|\bRTN\b|\bRFND\b|\*\*RETURN\*\*")
FOOTER_RE = re.compile(r"\bPAGE \d+ (?:OF|/) ?\d+\b|^\d+ ?/ ?\d+$")
HEADERISH_RE = re.compile(
    r"\(\d{3}\)|\d{3}[-. ]\d{3}[-. ]\d{4}|\bST#|\bOP#|\bTE#|\bTR#|\bTC#|\bMGR\b|\bCASHIER\b|\bSTORE ?#|\bMEMBER\b|"
    r"\bORDER ?#|\bORDER PLACED\b|\bDELIVERED\b|\bTEL\b|\bPHONE\b|\bWWW\.|\.COM\b|THANK YOU|\bOPEN \d|"
    r"\b\d{1,5} [A-Z]+ (?:ST|AVE|RD|BLVD|DR|PL|HWY|ROUTE|WAY|LN)\b|\b[A-Z]{2} \d{5}\b|RECEIPT|\bINVOICE\b"
)
DATE_HINT_RE = re.compile(r"DATE|PLACED|DELIVERED|ORDERED|PURCHASED|PICKED UP")

_MONTHS = {
    m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)
}

# --- quantity grammar ---------------------------------------------------------------------------
_NUM = r"\d+(?:[.,]\d{1,3})?"
_PRICE = r"\$?\s?\d*[.,]\d{2,3}"
WEIGHT_RE = re.compile(
    rf"^(?:QTY\s*)?(?P<q>{_NUM})\s*(?P<unit>LBS?|KG)\s*(?:@|X|AT)?\s*(?:1\s*(?:LB|KG)\s*/\s*)?(?P<u>{_PRICE})"
    rf"\s*(?:/\s*(?:LB|KG)|EA|EACH)?\s*(?P<rest>.*)$"
)
COUNT_RE = re.compile(
    rf"^(?:QTY\s*:?\s*)?(?P<q>\d{{1,3}})\s*(?:@|X|AT)\s*(?:1\s*FOR\s*)?(?P<u>{_PRICE})\s*(?:EA|EACH|/\s*EA)?\s*(?P<rest>.*)$"
)
WEAK_COUNT_RE = re.compile(rf"^(?P<q>\d{{1,2}})\s+[8&]\s+(?P<u>{_PRICE})\s*(?P<rest>.*)$")  # "@" read as 8 or &
QTY_ONLY_RE = re.compile(r"^(?:QTY|QUANTITY)\s*:?\s*(?P<q>\d{1,3})$")
UNIT_ONLY_RE = re.compile(rf"^(?P<u>{_PRICE})\s*(?:/\s*LB|/\s*KG|EACH|EA)$")
EMBEDDED_QTY_RE = re.compile(
    rf"\s(?:QTY\s*)?(?P<q>{_NUM})\s*(?P<unit>LBS?|KG)?\s*(?:@|X)\s*(?P<u>{_PRICE})\s*(?:/\s*(?:LB|KG)|EA|EACH)?\s*$"
)
INLINE_QTY_RE = re.compile(r"\s(?:QTY|QTY:)\s*(?P<q>\d{1,3})\s*$")
UNIT_FIX_RE = re.compile(r"^(?P<n>[0-9OSIlZB]{1,4})(?P<unit>ML|LB|LBS|CT|PK|OZ|GAL|QT|L)$")
OZ_FIX_RE = re.compile(r"^(?P<n>\d{1,3})[0O][Z2%7]{1,2}$")


@dataclass
class Draft:
    """An item being assembled from one or more lines."""

    name_parts: list[str]
    raw: list[str]
    total: int
    cert: int  # parse certainty, thousandths
    ocr: int  # lowest OCR confidence of its lines, thousandths
    caps: bool
    code: str | None = None
    taxable: bool | None = None
    quantity: Decimal | None = None
    unit: int | None = None
    qty_cert: int = FULL
    discount: int = 0
    refund: bool = False
    seam: bool = False
    lead_qty: Decimal | None = None


@dataclass
class QtyMod:
    q: Decimal
    unit: int
    line: Line
    weak: bool
    rest_total: int | None = None


@dataclass
class State:
    profile: StoreProfile
    drafts: list[Draft] = field(default_factory=list)
    fees: list[ExtractedFee] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    subtotal: int | None = None
    tax: int | None = None
    total: int | None = None
    total_conf: int = 0
    phase: str = "items"  # items | summary | done
    pending: list[Line] = field(default_factory=list)
    pending_mod: QtyMod | None = None
    pending_mod_prev: Draft | None = None
    price_col: Coord | None = None
    last_kind: str = ""
    first: Line | None = None  # the receipt's first line (a title, never part of an item name)


# --- small helpers --------------------------------------------------------------------------------


def _key(text: str) -> str:
    """Upper-case copy for keyword tests: punctuation (except $ % # & / @ .) becomes spaces."""
    t = text.upper().replace("’", "'")
    t = re.sub(r"[^A-Z0-9$%#&/@. ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _clean_text(text: str) -> str:
    # "�" is what pdfplumber and OCR give for an unencodable "×"; elsewhere it is noise.
    t = re.sub(r"(?<=[\dbB])\s*�\s*(?=\$?\d)", " x ", text).replace("�", " ")
    t = t.replace("×", " x ").replace("–", "-").replace("—", "-").replace(" ", " ")
    t = re.sub(r"(?<=\d)\s*[~](?=\s|$)", "-", t)  # "3.00~" is a trailing minus OCR'd as a tilde
    t = re.sub(r"\s+", " ", t).strip()
    return t.strip("|_`'\" ")


def _qty_text(text: str) -> str:
    """Normalize OCR in quantity lines: 1b/Ib → LB, & → @, x/× → X."""
    t = _key(text)
    t = re.sub(r"(?<=\d)\s*[1IL|]B\b", " LB", t)
    t = re.sub(r"/\s*[1IL|]B\b", "/LB", t)
    t = re.sub(r"\b[1IL|]B\s*/", "LB /", t)
    t = re.sub(r"(?<=\d)\s*&\s*(?=\d|\$|\.)", " @ ", t)
    t = re.sub(r"(?<=LB)\s*&\s*", " @ ", t)
    return re.sub(r"\s+", " ", t).strip()


def _money(token: str) -> int | None:
    r = repair_amount(token.replace(" ", ""))
    return r[0] if r else None


def _is_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 2:
        return True
    return sum(1 for c in letters if c.isupper()) * 10 >= len(letters) * 8


def _matches(q: Decimal, unit: int, total: int) -> bool:
    return abs(mul_cents(q, unit) - abs(total)) <= 1


# --- trailing amount ------------------------------------------------------------------------------


@dataclass
class Amount:
    left: str
    cents: int
    cert: int
    code: str


def split_amount(text: str, line: Line | None, profile: StoreProfile, price_col: Coord | None) -> Amount | None:
    """Split 'NAME ... 4.29 F' into ('NAME ...', 429, certainty, 'F')."""
    tokens = text.split(" ")
    if not tokens:
        return None
    code = ""
    codes = GENERIC_CODES | profile.taxable_codes | profile.nontaxable_codes
    if len(tokens) >= 2 and tokens[-1].upper() in codes and re.search(r"\d", tokens[-2]):
        code = tokens.pop().upper()
    m = re.match(r"^(.*\d)([A-Z]{1,2})$", tokens[-1])
    if m and m.group(2) in codes and repair_amount(m.group(1)) is not None:
        tokens[-1] = m.group(1)
        code = m.group(2)
    last = tokens[-1]
    left_tokens = tokens[:-1]
    # "$ 4.29", "- $1.00", "4. 29": glue the pieces OCR split apart.
    if left_tokens and left_tokens[-1] in ("$", "-", "-$", "S") and re.match(r"^\d*[.,]\d{2}-?$", last):
        last = left_tokens.pop() + last
    if left_tokens and re.fullmatch(r"\d+[.,]", left_tokens[-1]) and re.fullmatch(r"\d{2}-?", last):
        last = left_tokens.pop() + last
    if not code and left_tokens and left_tokens[-1].upper() in codes and len(left_tokens) >= 2:
        code = left_tokens.pop().upper()
    aligned = False
    if line is not None and price_col is not None and line.words:
        cw = char_width(line)
        lw = line.words[-1]
        gap_ok = len(line.words) >= 2 and lw.x0 - line.words[-2].x1 > cw * 2
        aligned = gap_ok and abs(lw.x1 - price_col) < cw * 3
    res = repair_amount(last, allow_missing_point=aligned)
    if res is None:
        return None
    cents, cert = res
    if not re.search(r"[.,]", last) and not aligned:
        return None
    return Amount(left=" ".join(left_tokens).strip(), cents=cents, cert=cert, code=code)


# --- names ----------------------------------------------------------------------------------------


@dataclass
class NameInfo:
    name: str
    code: str | None
    refund: bool
    exempt: bool
    lead_qty: Decimal | None


def _fix_token(tok: str, prev: str | None) -> str:
    """OCR repairs inside caps names: 32OZ read as 3202/320Z/32O%, S00ML, '24 02'."""
    up = tok.upper()
    m = OZ_FIX_RE.match(up)
    if m:
        return f"{m.group('n')}OZ"
    m = UNIT_FIX_RE.match(up)
    if m and re.search(r"\d", m.group("n")):
        n = m.group("n").translate(str.maketrans("OSIlZB", "051128"))
        return f"{n}{m.group('unit')}"
    if up in ("02", "03", "0Z", "OZ", "0%", "O%") and prev is not None and re.fullmatch(r"\d+(?:\.\d+)?", prev):
        return "OZ"
    if re.fullmatch(r"[A-Z]*0[A-Z]+|[A-Z]+0", up):
        # A letter word with a zero OCR'd into it ("0J", "B0WL"): zero back to O.
        return up.replace("0", "O")
    return tok


def _expand(text: str, profile: StoreProfile) -> str:
    raw_tokens = text.split()
    tokens: list[str] = []
    for i, tok in enumerate(raw_tokens):
        tokens.append(_fix_token(tok, raw_tokens[i - 1] if i else None))
    out: list[str] = []
    i = 0
    brands = {k.upper(): v for k, v in profile.brands.items()}
    while i < len(tokens):
        tok = tokens[i].upper().strip(",.")
        two = f"{tok} {tokens[i + 1].upper()}" if i + 1 < len(tokens) else ""
        if two in brands or two in ABBREVIATIONS:
            out.append(brands.get(two) or ABBREVIATIONS[two])
            i += 2
            continue
        if i == 0 and tok in brands:
            out.append(brands[tok])
        elif tok in ABBREVIATIONS:
            out.append(ABBREVIATIONS[tok])
        else:
            m = re.fullmatch(r"(\d+(?:\.\d+)?)(OZ|LB|LBS|CT|PK|GAL|ML|QT|L)", tok)
            out.append(f"{m.group(1)} {m.group(2).lower()}" if m else tok.lower())
        i += 1
    name = " ".join(out)
    return name[:1].upper() + name[1:]


def _fix_mixed(s: str) -> str:
    """OCR repairs for unit words in mixed-case e-receipt names: '10 Ib', '12 0z', '64 fi oz', '500 mI'."""
    s = re.sub(r"(?<=\d)\s?(?:Ib|IB|\|b)\b", " lb", s)
    s = re.sub(r"(?<=\d )1b\b", "lb", s)
    s = re.sub(r"(?<=\d )(?:0z|02|0Z|Oz)\b", "oz", s)
    s = re.sub(r"\b(?:fi|fI|f1|FI)(?= oz\b)", "fl", s)
    s = re.sub(r"(?<=\d )(?:gt|qi)\b", "qt", s)
    return re.sub(r"(?<=\d)(\s?)(?:mI|m1|mi)\b", r"\1ml", s)


def clean_name(left: str, profile: StoreProfile, *, caps: bool) -> NameInfo:
    s = re.sub(r"[|_~`«»]", " ", left)
    s = re.sub(r"\s+", " ", s).strip()
    exempt = False
    code: str | None = None
    if profile.exempt_prefix:
        m = re.match(r"^[E£€]\s+(?=\d)", s)  # OCR reads the exempt "E" as £ or €
        if m:
            exempt = True
            s = s[m.end() :]
    if profile.item_code:
        m = re.search(profile.item_code, s)
        if m:
            code = re.sub(r"\D", "", m.group(0))
            s = (s[: m.start()] + " " + s[m.end() :]).strip()
    for m in list(re.finditer(r"\b\d{8,14}\b", s)):  # UPCs and other long codes anywhere
        code = code or m.group(0)
    s = re.sub(r"\b\d{8,14}\b", " ", s)
    lead_qty: Decimal | None = None
    if profile.leading_qty:
        m = re.match(r"^(\d{1,2}(?:\.\d{1,3})?)\s*(?:[lI1|][bB]S?|LBS?)(?=[\sA-Z])\s*", s) or re.match(
            r"^(\d{1,2}(?:\.\d{1,3})?)\s+(?=\S)", s
        )
        if m:
            lead_qty = parse_quantity(m.group(1))
            s = s[m.end() :]
    refund = bool(REFUND_RE.search(_key(s)))
    if refund:
        s = re.sub(r"\*{0,2}\b(?:RETURN(?:ED)?|REFUND(?:ED)?|RTN|RFND)\b\*{0,2}", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" -:,*#.")
    if not caps:
        s = _fix_mixed(s)
    if caps:
        # Register flags left between the name and the price (Walmart "F"/"KF").
        s = re.sub(r"(?:\s(?:KF|F|NF|TF|N|X|T))+$", "", s)
        s = _expand(s, profile)
    return NameInfo(name=s.strip(" ,"), code=code, refund=refund, exempt=exempt, lead_qty=lead_qty)


def _headerish(line: Line) -> bool:
    k = _key(line.text)
    words = set(k.split())
    if not words:
        return True
    if FOOTER_RE.search(k) or HEADERISH_RE.search(k) or PAYMENT_RE.search(k) or INFO_RE.search(k):
        return True
    if k in DEPARTMENTS or words <= DEPARTMENTS | {"AND", "&"}:
        return True
    if words <= TABLE_HEADER_WORDS:
        return True
    if _date_in(line.text) is not None or re.search(r"\b\d{1,2}:\d{2}\b", k):
        return True
    return sum(c.isalnum() for c in k) < 2


# --- dates ----------------------------------------------------------------------------------------


def _date_in(text: str) -> date | None:
    t = text.upper()
    for m in re.finditer(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t):
        d = _mk_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            return d
    for m in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})\b", t):
        y = int(m.group(3))
        d = _mk_date(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2)))
        if d:
            return d
    for m in re.finditer(r"\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b", t):
        d = _mk_date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
        if d:
            return d
    for m in re.finditer(r"\b(\d{1,2})\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\.?,?\s+(\d{4})\b", t):
        d = _mk_date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
        if d:
            return d
    return None


def _mk_date(y: int, mo: int, d: int) -> date | None:
    if not 2000 <= y <= 2099:
        return None
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def find_date(lines: Sequence[Line]) -> tuple[date | None, int]:
    found: list[tuple[int, date, int]] = []
    for ln in lines:
        d = _date_in(ln.text)
        if d:
            found.append((0 if DATE_HINT_RE.search(ln.text.upper()) else 1, d, ln.confidence))
    if not found:
        return None, 0
    found.sort(key=lambda f: f[0])
    _, d, conf = found[0]
    return d, min(950, conf)


def find_store(lines: Sequence[Line], profile: StoreProfile, profile_conf: int) -> tuple[str | None, int]:
    if profile is not GENERIC:
        return profile.display_name, profile_conf
    for ln in lines[:6]:
        k = _key(ln.text)
        if sum(c.isalpha() for c in k) >= 3 and not HEADERISH_RE.search(k) and _date_in(ln.text) is None:
            name = re.sub(r"\s+", " ", ln.text).strip(" *#-")
            return (name.title() if name.isupper() else name), 600
    return None, 0


# --- the parser -----------------------------------------------------------------------------------


def _attach_mod(st: State, d: Draft, mod: QtyMod, *, matched: bool) -> None:
    d.quantity = mod.q
    d.unit = mod.unit
    if not matched:
        d.qty_cert = 700
    d.ocr = min(d.ocr, mod.line.confidence)


def _new_item(st: State, line: Line, amt: Amount, name_lines: list[Line]) -> Draft | None:
    left = amt.left
    caps = _is_caps(" ".join([*(p.text for p in name_lines), left]))
    if name_lines:
        left = " ".join([*(_clean_text(p.text) for p in name_lines), left])
    # quantity inside the line: "LIMES 3 @ 0.50 1.50", "QTY 2"
    qty: Decimal | None = None
    unit: int | None = None
    m = EMBEDDED_QTY_RE.search(_qty_text(" " + left))
    if m:
        q = parse_quantity(m.group("q").replace(",", "."))
        u = _money(m.group("u"))
        if q is not None and u is not None:
            qty, unit = q, u
            cut = re.search(r"\s(?:QTY\s*)?" + re.escape(m.group("q").split(".")[0]) + r"[\s.,]", " " + left)
            left = (" " + left)[: cut.start()].strip() if cut else left
    else:
        m2 = INLINE_QTY_RE.search(" " + _key(left))
        if m2:
            qty = parse_quantity(m2.group("q"))
            left = re.sub(r"\s(?:QTY|QTY:)\s*\d{1,3}\s*$", "", " " + left, flags=re.IGNORECASE).strip()
    info = clean_name(left, st.profile, caps=caps)
    if sum(c.isalpha() for c in info.name) < 2:
        return None
    ocr = min([line.confidence, *(p.confidence for p in name_lines)])
    if ocr < MIN_OCR_CONF_FOR_ITEM:
        st.warnings.append(f"unreadable_line: {line.text[:40]}")
        return None
    taxable: bool | None = None
    if info.exempt:
        taxable = False
    elif amt.code in st.profile.taxable_codes:
        taxable = True
    elif amt.code in st.profile.nontaxable_codes:
        taxable = False
    total = amt.cents
    if info.refund and total > 0:
        total = -total
    d = Draft(
        name_parts=[info.name],
        raw=[*(p.text for p in name_lines), line.text],
        total=total,
        cert=amt.cert,
        ocr=ocr,
        caps=caps,
        code=info.code,
        taxable=taxable,
        refund=info.refund or total < 0,
        seam=line.seam or any(p.seam for p in name_lines),
        lead_qty=info.lead_qty,
    )
    if qty is not None:
        d.quantity, d.unit = qty, unit
        if unit is not None and not _matches(qty, unit, total):
            d.qty_cert = 700
    elif info.lead_qty is not None:
        d.quantity = info.lead_qty
        d.unit = None
    return d


def _is_store_line(line: Line, profile: StoreProfile) -> bool:
    compact = re.sub(r"[^A-Z0-9&*.'\-]", "", line.text.upper())
    return any(re.search(p, compact) for p in profile.patterns)


def _wrap_candidates(st: State, line: Line) -> list[Line]:
    """Pending name-only lines that belong to this priced line (a wrapped long name)."""
    out: list[Line] = []
    nxt = line
    for p in reversed(st.pending[-2:]):
        lead = st.profile.leading_qty and re.match(r"^\d{1,2}(?:\.\d{1,3})?\s", p.text) is not None
        if p.page != nxt.page or p is st.first or _headerish(p):
            break
        if not lead and not st.drafts and _is_store_line(p, st.profile):
            break  # the store's own name above the first item
        height = max(nxt.bottom - nxt.top, p.bottom - p.top, 1)
        close = 0 <= nxt.top - p.top <= height * 5 / 2
        cw = char_width(p)
        aligned = abs(p.x0 - nxt.x0) <= cw * 2
        cut_price = re.search(r"\d[.,]\d{2,3}\s?\S?$", p.text) is not None or (
            st.price_col is not None and abs(p.x1 - st.price_col) < cw * 3 and re.search(r"\d\S*$", p.text) is not None
        )
        if cut_price:
            # A priced line whose price could not be read, not the first half of a long name.
            st.warnings.append(f"unreadable_price: {p.text[:40]}")
            break
        if not close or not (aligned or lead):
            break
        out.insert(0, p)
        nxt = p
    return out


def _parse_mod(line: Line) -> QtyMod | None:
    t = _qty_text(_clean_text(line.text))
    for rx, weak in ((WEIGHT_RE, False), (COUNT_RE, False), (WEAK_COUNT_RE, True)):
        m = rx.match(t)
        if not m:
            continue
        q = parse_quantity(m.group("q").replace(",", "."))
        u = _money(m.group("u"))
        if q is None or u is None:
            continue
        rest = m.group("rest").strip()
        rest_total = _money(rest.split(" ")[-1]) if rest else None
        if rest and rest_total is None and re.search(r"[A-Z]{3,}", rest):
            continue  # "2 X LARGE ..." is a name, not a quantity line
        return QtyMod(q=q, unit=u, line=line, weak=weak, rest_total=rest_total)
    m = QTY_ONLY_RE.match(t)
    if m:
        q = parse_quantity(m.group("q"))
        if q is not None:
            return QtyMod(q=q, unit=0, line=line, weak=False)
    return None


def _handle_mod(st: State, mod: QtyMod) -> None:
    prev = st.drafts[-1] if st.drafts and st.last_kind in ("item", "discount") else None
    if mod.rest_total is not None and st.pending and not _headerish(st.pending[-1]):
        # "BANANAS" / "2 @ 0.29   0.58": the quantity line carries the total for the name above it.
        name_line = st.pending[-1]
        d = _new_item(
            st, mod.line, Amount(left=_clean_text(name_line.text), cents=mod.rest_total, cert=FULL, code=""), []
        )
        st.pending = []
        if d:
            d.raw.insert(0, name_line.text)
            d.quantity, d.unit = mod.q, mod.unit
            st.drafts.append(d)
            st.last_kind = "item"
        return
    if mod.unit == 0 and prev is not None:  # "QTY 2" below an item
        prev.quantity = mod.q
        st.last_kind = "mod"
        return
    if prev is not None and prev.quantity is None and _matches(mod.q, mod.unit, prev.total):
        _attach_mod(st, prev, mod, matched=True)
        st.last_kind = "mod"
        return
    st.pending_mod = mod
    st.pending_mod_prev = prev if prev is not None and prev.quantity is None else None
    st.last_kind = "mod"


def _resolve_pending_mod(st: State, new: Draft | None) -> None:
    mod = st.pending_mod
    if mod is None:
        return
    st.pending_mod = None
    prev = st.pending_mod_prev
    st.pending_mod_prev = None
    if new is not None and new.quantity is None and _matches(mod.q, mod.unit, new.total):
        _attach_mod(st, new, mod, matched=True)
        return
    if mod.weak:
        return  # an ambiguous "4 8 0.99" only counts when the arithmetic proves it
    target = new if st.profile.qty_position == "above" else prev
    target = target or new or prev
    if target is not None and target.quantity is None:
        _attach_mod(st, target, mod, matched=False)


def _add_fee(st: State, kind: str, label: str, cents: int, conf: int) -> None:
    label = re.sub(r"\s+", " ", label).strip(" :*-") or ("Tip" if kind == "tip" else "Fee")
    if kind == "fee" and re.search(r"DEPOSIT|\bDEP\b|CRV|REDEMPTION", label.upper()):
        label = "Bottle deposit"
    label = label[:1].upper() + label[1:].lower() if label.isupper() else label
    if cents == 0:
        return
    fee_kind: FeeKind = "tip" if kind == "tip" else "order_discount" if kind == "order_discount" else "fee"
    st.fees.append(ExtractedFee(kind=fee_kind, label=label[:120], amount_cents=abs(cents), confidence_x1000=conf))


def _discount(st: State, line: Line, amt: Amount, key: str) -> None:
    value = abs(amt.cents)
    conf = min(amt.cert, line.confidence)
    ref = re.search(r"/\s*(\d{4,8})\b", amt.left)
    target: Draft | None = None
    if ref:
        target = next((d for d in reversed(st.drafts) if d.code == ref.group(1)), None)
    if (
        target is None
        and st.drafts
        and st.last_kind in ("item", "mod", "discount", "fee")
        and not ORDER_DISCOUNT_RE.search(key)
    ):
        target = st.drafts[-1]
    if st.phase != "items" or target is None or value > abs(target.total) - target.discount or target.refund:
        label = re.sub(r"[-$\d.,/]+$", "", amt.left).strip() or "Discount"
        _add_fee(st, "order_discount", label, value, conf)
        return
    target.discount += value
    target.ocr = min(target.ocr, line.confidence)
    target.cert = min(target.cert, amt.cert)
    target.raw.append(line.text)


def parse_receipt(
    lines: Sequence[Line],
    *,
    source: ExtractionSource = "pdf_text",
    warnings: Iterable[str] = (),
) -> ExtractedBill:
    ordered = sorted(lines, key=lambda ln: (ln.page, ln.top, ln.x0))
    profile, profile_conf = detect_profile(ordered)
    st = State(profile=profile, warnings=list(warnings), first=ordered[0] if ordered else None)
    st.price_col = price_col = price_column(ordered)

    for line in ordered:
        if st.phase == "done":
            break
        text = _clean_text(line.text)
        if not text:
            continue
        key = _key(text)
        if FOOTER_RE.search(key):
            continue
        amt = split_amount(text, line, profile, price_col)
        mod = _parse_mod(line) if st.phase == "items" else None
        if amt is None or mod is not None:
            if mod is not None:
                _handle_mod(st, mod)
                continue
            unit_only = UNIT_ONLY_RE.match(_qty_text(text))
            if unit_only:
                # "$3.99 each" under an item whose quantity came from a column: its unit price.
                prev = st.drafts[-1] if st.drafts and st.last_kind == "item" else None
                if prev is not None and prev.unit is None:
                    prev.unit = _money(unit_only.group("u"))
                continue
            if INFO_RE.search(key):
                continue
            if st.phase == "items":
                st.pending.append(line)
                st.pending = st.pending[-3:]
            continue
        lkey = _key(amt.left)
        conf = min(amt.cert, line.confidence)
        if INFO_RE.search(lkey):
            continue
        if SUBTOTAL_RE.search(lkey) or _fuzzy_word(lkey, "SUBTOTAL"):
            _resolve_pending_mod(st, None)
            if st.subtotal is None:
                st.subtotal = amt.cents
            st.phase = "summary"
            st.pending = []
            continue
        if (
            TOTAL_RE.search(lkey) or _fuzzy_word(lkey, "TOTAL") or _fuzzy_word(lkey, "BALANCE")
        ) and not NOT_TOTAL_RE.search(lkey):
            _resolve_pending_mod(st, None)
            st.total = amt.cents
            st.total_conf = conf
            st.phase = "done"
            continue
        if TAX_RE.search(lkey) and not NOT_TAX_RE.search(lkey):
            _resolve_pending_mod(st, None)
            st.tax = (st.tax or 0) + amt.cents
            st.phase = "summary"
            st.pending = []
            continue
        if TIP_RE.search(lkey):
            _add_fee(st, "tip", amt.left, amt.cents, conf)
            st.pending = []
            continue
        if FEE_RE.search(lkey) and not (amt.cents < 0):
            _add_fee(st, "fee", amt.left, amt.cents, conf)
            st.pending = []
            st.last_kind = "fee"
            continue
        if PAYMENT_RE.search(lkey) and not REFUND_RE.search(lkey):
            continue
        if st.phase == "summary":
            if amt.cents < 0 or LINE_DISCOUNT_RE.search(lkey) or ORDER_DISCOUNT_RE.search(lkey):
                _add_fee(st, "order_discount", amt.left, amt.cents, conf)
            elif amt.cents:
                st.warnings.append(f"unrecognized_summary_line: {amt.left[:40]}")
            continue
        is_refund = bool(REFUND_RE.search(lkey)) or (
            amt.cents < 0 and any(REFUND_RE.search(_key(p.text)) for p in _wrap_candidates(st, line))
        )
        letters = sum(c.isalpha() for c in re.sub(r"\b\d{3}-\d{2}-\d{4}\b|/\d+", "", amt.left))
        if not is_refund and (
            amt.cents < 0 or (POSITIVE_DISCOUNT_RE.search(lkey) and not re.search(r"\d{5,}", lkey)) or letters < 2
        ):
            if amt.cents < 0 or POSITIVE_DISCOUNT_RE.search(lkey) or "/" in amt.left:
                _resolve_pending_mod(st, None)
                _discount(st, line, amt, lkey)
                st.last_kind = "discount"
                st.pending = []
            continue
        if HEADERISH_RE.search(lkey):
            continue
        wrap = _wrap_candidates(st, line)
        st.pending = []
        d = _new_item(st, line, amt, wrap)
        if d is None:
            continue
        st.drafts.append(d)
        _resolve_pending_mod(st, d)
        st.last_kind = "item"

    _resolve_pending_mod(st, None)
    return _finish(st, ordered, source, profile, profile_conf)


def _fuzzy_word(key: str, word: str) -> bool:
    """OCR-tolerant keyword: one token within one edit of `word` (only for words of 5+ letters)."""
    from difflib import SequenceMatcher

    for tok in key.split():
        tok = tok.strip("*#.:")
        if len(tok) >= 5 and abs(len(tok) - len(word)) <= 1 and SequenceMatcher(None, tok, word).ratio() * 100 >= 85:
            return True
    return False


def _merge_fees(fees: list[ExtractedFee]) -> list[ExtractedFee]:
    """One row per (kind, label): five bottle deposits become one 'Bottle deposit' fee."""
    merged: dict[tuple[str, str], ExtractedFee] = {}
    for f in fees:
        k = (f.kind, f.label.lower())
        if k in merged:
            m = merged[k]
            merged[k] = m.model_copy(
                update={
                    "amount_cents": m.amount_cents + f.amount_cents,
                    "confidence_x1000": min(m.confidence_x1000, f.confidence_x1000),
                }
            )
        else:
            merged[k] = f
    return list(merged.values())


def _finish(
    st: State, lines: Sequence[Line], source: ExtractionSource, profile: StoreProfile, profile_conf: int
) -> ExtractedBill:
    items: list[ExtractedItem] = []
    for d in st.drafts[:MAX_ITEMS]:
        name = " ".join(p for p in d.name_parts if p).strip()
        if not name:
            continue
        qty = normalize_quantity(d.quantity) if d.quantity is not None else Decimal(1)
        unit = d.unit if d.unit else None
        if unit is None and qty == 1:
            unit = abs(d.total)
        cert = min(d.cert, d.qty_cert)
        conf = cert * d.ocr // FULL
        if d.seam:
            conf = min(conf, SEAM_CONF)
        c = classify(name)
        items.append(
            ExtractedItem(
                name=name[:200],
                raw_text=" | ".join(d.raw)[:2000],
                quantity=qty,
                unit_price_cents=unit,
                line_total_cents=d.total,
                discount_cents=d.discount,
                taxable=True if d.taxable is None else d.taxable,
                confidence_x1000=max(0, min(conf, FULL)),
                storage=c.storage,
                category=c.category,
                icon_key=c.icon_key,
                shelf_life_days=c.shelf_life_days,
            )
        )
    if len(st.drafts) > MAX_ITEMS:
        st.warnings.append(f"too_many_items: kept {MAX_ITEMS} of {len(st.drafts)}")
    store, store_conf = find_store(lines, profile, profile_conf)
    when, date_conf = find_date(lines)
    bill = ExtractedBill(
        store_name=store,
        purchase_date=when,
        items=items,
        subtotal_cents=st.subtotal,
        tax_cents=st.tax,
        fees=_merge_fees(st.fees),
        total_cents=st.total,
        field_confidence_x1000={"store_name": store_conf, "purchase_date": date_conf, "total": st.total_conf},
        warnings=st.warnings,
        source=source,
    ).recompute()
    if bill.subtotal_cents is not None:
        # Registers often ring deposits and bag fees into the subtotal: only warn when neither reading fits.
        items_sum = sum(i.net_cents for i in bill.items)
        in_fees = sum(f.amount_cents for f in bill.fees if f.kind == "fee")
        if bill.subtotal_cents in (items_sum, items_sum + in_fees) or any(
            bill.subtotal_cents == items_sum + f.amount_cents for f in bill.fees if f.kind == "fee"
        ):
            bill.warnings = [w for w in bill.warnings if not w.startswith("subtotal_mismatch")]
    if bill.total_cents is None:
        bill.warnings.append("no_total_found")
    flagged = sum(1 for i in bill.items if i.flagged)
    if not bill.items and bill.total_cents is None:
        bill.status = "failed"
        bill.warnings.append("not_a_receipt_or_unreadable")
    elif bill.total_cents is None or flagged * 3 >= max(len(bill.items), 1):
        bill.status = "low_confidence"
    return bill


def parse_text(text: str, *, source: ExtractionSource = "pdf_text") -> ExtractedBill:
    """Convenience for tests and manual entry: one receipt line per text line."""
    from app.extraction.lines import lines_from_text

    return parse_receipt(lines_from_text(text), source=source)
