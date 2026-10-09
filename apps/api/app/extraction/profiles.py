"""Store profiles and receipt shorthand (BUILD_ALL C1 step 3).

A profile is a few layout facts about one chain's receipts: how to recognise it, its own-brand
shorthand, where quantity lines sit, what its tax-flag letters mean and what leading codes it
prints. Everything else is the generic grammar in `parser.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

from app.extraction.lines import Line


@dataclass(frozen=True, slots=True)
class StoreProfile:
    key: str
    display_name: str
    patterns: tuple[str, ...]  # regexes on a compact upper-case header line
    brands: dict[str, str] = field(default_factory=dict)  # store-brand shorthand → name
    qty_position: Literal["above", "below"] = "below"
    leading_qty: bool = False  # a quantity column before the name (Amazon invoices)
    taxable_codes: frozenset[str] = frozenset({"T", "X", "TX", "A"})
    nontaxable_codes: frozenset[str] = frozenset({"N", "F", "NF", "E", "FN", "KF", "O"})
    item_code: str | None = None  # regex for a leading/inline product code to strip (kept in raw_text)
    exempt_prefix: bool = False  # Costco prints "E" before tax-exempt items


GENERIC = StoreProfile("generic", "", ())

PROFILES: tuple[StoreProfile, ...] = (
    StoreProfile("instacart", "Instacart", (r"INSTACART",), qty_position="below"),
    StoreProfile(
        "amazon",
        "Whole Foods Market",
        (r"WHOLEFOODS", r"AMAZON", r"AMAZONFRESH"),
        qty_position="below",
        leading_qty=True,
    ),
    StoreProfile(
        "walmart",
        "Walmart",
        (r"WALMART", r"WAL\*?MART", r"SAVEMONEY\.?LIVEBETTER"),
        brands={"GV": "Great Value", "EQ": "Equate", "MM": "Marketside"},
        qty_position="below",
        taxable_codes=frozenset({"X", "T", "A"}),
        nontaxable_codes=frozenset({"N", "O", "F", "KF"}),
        item_code=r"\b\d{11,13}\b",
    ),
    StoreProfile(
        "target",
        "Target",
        (r"^TARGET$", r"TARGET\.COM", r"EXPECTMORE"),
        brands={"GG": "Good & Gather", "MKT PANTRY": "Market Pantry", "UP&UP": "up & up"},
        qty_position="below",
        taxable_codes=frozenset({"T"}),
        nontaxable_codes=frozenset({"N", "NF", "F"}),
        item_code=r"^\d{3}[-~ ]?\d{2}[-~ ]?\d{4}\b",  # DPCI; OCR reads a dash as "~"
    ),
    StoreProfile(
        "costco",
        "Costco",
        (r"COSTCO", r"WHOLESALE"),
        brands={"KS": "Kirkland Signature", "KIRKLAND": "Kirkland Signature"},
        qty_position="above",
        taxable_codes=frozenset({"A", "Y", "T"}),
        nontaxable_codes=frozenset({"N", "E"}),
        item_code=r"^\d{4,8}\b",
        exempt_prefix=True,
    ),
    StoreProfile(
        "shoprite",
        "ShopRite",
        (r"SHOPRITE", r"PRICEPLUS"),
        brands={"SR": "ShopRite", "BWL&BSKT": "Bowl & Basket"},
        qty_position="above",
        taxable_codes=frozenset({"T", "TF", "TX"}),
        nontaxable_codes=frozenset({"F", "N"}),
    ),
    StoreProfile(
        "traderjoes",
        "Trader Joe's",
        (r"TRADERJOE",),
        brands={"TJ'S": "Trader Joe's", "TJS": "Trader Joe's"},
        qty_position="below",
    ),
    StoreProfile("hmart", "H Mart", (r"^HMART", r"H-?MART"), qty_position="below"),
)

# Generic grocery shorthand (word → expansion). Applied only to ALL-CAPS register text; e-receipts
# print real names. Keys are upper case; multi-word keys are matched before single words.
ABBREVIATIONS: dict[str, str] = {
    "ORG": "organic", "BNNA": "bananas", "BNNAS": "bananas", "BAN": "bananas", "GRK": "Greek", "YGRT": "yogurt",
    "YOG": "yogurt", "YGT": "yogurt", "PLN": "plain", "LG": "large", "LRG": "large", "SM": "small", "MED": "medium",
    "SHRD": "shredded", "SHRED": "shredded", "CHED": "cheddar", "CHDR": "cheddar", "CHS": "cheese", "UNSLT": "unsalted",
    "SLTD": "salted", "MOZZ": "mozzarella", "PARM": "parmesan", "OJ": "orange juice", "BNLS": "boneless",
    "SKNLS": "skinless", "CHKN": "chicken", "CHIX": "chicken", "BRST": "breast", "THGH": "thigh", "THGHS": "thighs",
    "GRND": "ground", "BF": "beef", "ATL": "Atlantic", "BBY": "baby", "SDLS": "seedless", "WHL": "whole",
    "WHT": "wheat", "WW": "whole wheat", "CRMY": "creamy", "PNT": "peanut", "PNUT": "peanut",
    "EVOO": "extra virgin olive oil", "RSTD": "roasted", "CHOC": "chocolate", "SPRKLNG": "sparkling",
    "SPRK": "sparkling", "FRZ": "frozen", "FRZN": "frozen", "LNDRY": "laundry", "DET": "detergent",
    "TP": "toilet paper",
    "PPR": "paper", "TWL": "towel", "TWLS": "towels", "STRWB": "strawberries", "STRAWB": "strawberries",
    "BLUEB": "blueberries", "AVOC": "avocado", "TOM": "tomatoes", "TOMS": "tomatoes", "ONN": "onions", "GRLC": "garlic",
    "POT": "potatoes", "POTS": "potatoes", "BRD": "bread", "SLCD": "sliced", "VEG": "vegetable", "VEGS": "vegetables",
    "SCE": "sauce", "MRNRA": "marinara", "PSTA": "pasta", "SPAG": "spaghetti", "RCE": "rice", "BSMTI": "basmati",
    "CRL": "cereal", "OATML": "oatmeal", "CFE": "coffee", "COF": "coffee", "CRM": "cream", "ICE CRM": "ice cream",
    "VAN": "vanilla", "VNLA": "vanilla", "CKIE": "cookies", "CKIES": "cookies", "CHPS": "chips", "TORT": "tortilla",
    "TRTLA": "tortilla", "HMS": "hummus", "TFU": "tofu", "KMCH": "kimchi", "DMPLG": "dumplings", "NDL": "noodles",
    "NDLS": "noodles", "WTR": "water", "PROT": "protein", "BTR": "butter", "BTTR": "butter", "MLK": "milk",
    "2%": "2%", "SKM": "skim", "GAL": "gal", "HG": "half gallon", "DZ": "dozen", "CT": "ct", "PK": "pk",
    "OZ": "oz", "LB": "lb", "LBS": "lb", "QT": "qt", "PT": "pint", "FL": "fl", "LTR": "liter", "SDA": "soda",
    "BTL": "bottle", "BTLS": "bottles", "CN": "can", "CNS": "cans", "GRN": "green", "RD": "red", "YLW": "yellow",
    "WHTE": "white", "BRWN": "brown", "FRSH": "fresh", "NAT": "natural", "ORIG": "original", "UNSWT": "unsweetened",
    "SWT": "sweet", "BRKFST": "breakfast", "SNDWCH": "sandwich", "SAUS": "sausage", "PEPP": "pepperoni",
    "MUSHRM": "mushroom", "MSHRM": "mushrooms", "SPNCH": "spinach", "CUC": "cucumber", "BRCLI": "broccoli",
    "LETT": "lettuce", "ROM": "romaine", "CILNTR": "cilantro", "PRSLY": "parsley", "JCE": "juice", "APPL": "apple",
    "APPLS": "apples", "GRPS": "grapes", "CHRY": "cherry", "LMN": "lemon", "LMNS": "lemons", "STKS": "steaks",
    "PCS": "pieces", "ASST": "assorted", "VAR": "variety", "MINI": "mini", "XL": "extra large", "DBL": "double",
    "RL": "roll", "RLS": "rolls", "SPNG": "sponge", "SPNGS": "sponges", "DSH": "dish", "SP": "soap", "BG": "bag",
    "BGS": "bags", "TRSH": "trash",
}  # fmt: skip

# Words that are not food names: department headers and table headers printed as their own line.
DEPARTMENTS = frozenset(
    [
        "GROCERY",
        "GROCERIES",
        "DAIRY",
        "PRODUCE",
        "MEAT",
        "MEATS",
        "SEAFOOD",
        "DELI",
        "BAKERY",
        "FROZEN",
        "HOUSEHOLD",
        "HOME",
        "BEAUTY",
        "HEALTH",
        "PHARMACY",
        "BEVERAGES",
        "DRINKS",
        "SNACKS",
        "PANTRY",
        "ELECTRONICS",
        "APPAREL",
        "CLOTHING",
        "BABY",
        "PETS",
        "PET",
        "GENERAL",
        "MERCHANDISE",
        "HEALTH",
        "AND",
        "BEAUTY",
        "HBC",
        "MISC",
        "FLORAL",
        "LIQUOR",
        "WINE",
        "BEER",
        "CLEANING",
        "PAPER",
    ]
)
TABLE_HEADER_WORDS = frozenset(
    [
        "ITEM",
        "ITEMS",
        "QTY",
        "QUANTITY",
        "PRICE",
        "DESCRIPTION",
        "AMOUNT",
        "TOTAL",
        "UNIT",
        "EACH",
        "SUBTOTAL",
        "PRODUCT",
        "PRODUCTS",
    ]
)


def _compact(text: str) -> str:
    return re.sub(r"[^A-Z0-9&*.'\-]", "", text.upper())


def detect_profile(lines: list[Line]) -> tuple[StoreProfile, int]:
    """The profile whose header patterns match the top of the receipt (fuzzy, for OCR), with a
    confidence in thousandths."""
    head = [ln for ln in lines[:12]]
    for profile in PROFILES:
        for ln in head:
            compact = _compact(ln.text)
            for pat in profile.patterns:
                if re.search(pat, compact):
                    return profile, 970
    # OCR-tolerant second pass over the first lines: near-matches of the plain store words.
    for profile in PROFILES:
        target = re.sub(r"[^A-Z]", "", profile.display_name.upper())
        for ln in head[:6]:
            word = re.sub(r"[^A-Z]", "", ln.text.upper())
            if len(word) >= 4 and SequenceMatcher(None, word, target).ratio() * 10 >= 8:
                return profile, 850
    # Anywhere in the receipt (footers like "THANK YOU FOR SHOPPING AT TRADER JOE'S").
    for profile in PROFILES:
        for ln in lines:
            compact = _compact(ln.text)
            if any(re.search(p, compact) for p in profile.patterns if not p.startswith("^")):
                return profile, 800
    return GENERIC, 0
