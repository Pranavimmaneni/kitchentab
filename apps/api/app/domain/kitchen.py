"""Kitchen defaults (PRD §19.5, §19.7): shelf life, storage mapping, tile names."""

from __future__ import annotations

# Default shelf life, by icon first, then category (the §19.5 table). Product defaults, not
# food-safety advice: the UI says "probably".
SHELF_LIFE_BY_ICON = {
    "chicken": 2, "beef": 2, "fish": 2, "leafy_greens": 5, "berries": 5, "leftovers": 4, "milk": 7,
    "plant_milk": 7, "tofu": 7, "yogurt": 14, "eggs": 28, "kimchi_jar": 60, "banana": 6, "apple": 6,
    "citrus": 6, "bread": 7, "tortillas": 7, "coffee": 180, "tea": 180, "rice": 365, "pasta": 365,
    "noodles": 365, "canned": 365, "beans": 365, "frozen_bag": 90, "ice_cream": 90, "dumplings": 90,
    "pizza": 90,
}  # fmt: skip
SHELF_LIFE_BY_CATEGORY = {
    "meat_fish": 2, "produce": 5, "prepared": 4, "dairy": 7, "drinks": 7, "plant_protein": 7, "eggs": 28,
    "frozen": 90, "bakery": 7, "coffee_tea": 180, "grains": 365, "canned": 365, "snacks": 180,
    "condiments": 180, "other": 30,
}  # fmt: skip
SHORT_NAME_MAX = 14


def default_shelf_life(category: str | None, icon: str | None) -> int:
    if icon in SHELF_LIFE_BY_ICON:
        return SHELF_LIFE_BY_ICON[icon]
    return SHELF_LIFE_BY_CATEGORY.get(category or "other", 30)


def storage_for(category: str | None, storage: str) -> str:
    """`household` always maps to NOT FOOD (§19.7 rules)."""
    return "none" if category == "household" else storage


def place_for(storage: str) -> str:
    return storage  # fridge | freezer | pantry (the fridge view shows freezer as its own area)


def generic_icon(storage: str) -> str:
    return "generic_fridge" if storage in ("fridge", "freezer") else "generic_pantry"


def short_name(name: str) -> str:
    """Tile label: drop sizes and counts ("Whole milk 1 gal" -> "Whole milk"), fit 14 chars."""
    words = []
    for word in name.split():
        if any(ch.isdigit() for ch in word) or word.lower() in {"lb", "oz", "gal", "ct", "pk", "l", "ml", "kg", "g"}:
            break
        words.append(word)
    label = " ".join(words) or name
    if len(label) <= SHORT_NAME_MAX:
        return label
    first = label.split()[0]
    return first[:SHORT_NAME_MAX]
