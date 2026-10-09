"""Storage / category / icon / shelf life for an item name (PRD §19.7, EXTRACT-07).

A keyword table, most specific phrases first. Rules from §19.7: `household` always maps to
storage `none`; an unknown icon becomes `generic_fridge` or `generic_pantry` by storage; a missing
shelf life falls back to the category default (`app.domain.kitchen`). Not-food items get no shelf
life. The payer's tag on the review screen always wins over this guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.kitchen import default_shelf_life, generic_icon, storage_for

ICONS = frozenset(
    [
        "milk",
        "plant_milk",
        "eggs",
        "yogurt",
        "cheese",
        "butter",
        "juice",
        "soda",
        "water",
        "bread",
        "tortillas",
        "cereal",
        "oats",
        "rice",
        "pasta",
        "noodles",
        "flour",
        "oil",
        "sauce_jar",
        "condiment",
        "spices",
        "canned",
        "beans",
        "chips",
        "cookies",
        "bar",
        "chocolate",
        "nuts",
        "coffee",
        "tea",
        "banana",
        "apple",
        "berries",
        "citrus",
        "leafy_greens",
        "root_veg",
        "tomato",
        "onion_garlic",
        "herbs",
        "chicken",
        "beef",
        "fish",
        "tofu",
        "kimchi_jar",
        "leftovers",
        "frozen_bag",
        "ice_cream",
        "dumplings",
        "pizza",
        "generic_fridge",
        "generic_pantry",
    ]
)


@dataclass(frozen=True, slots=True)
class Kind:
    category: str
    storage: str
    icon: str | None  # None → generic icon by storage


# (phrases, kind). A phrase matches when all its words appear in order as whole words.
_RULES: list[tuple[tuple[str, ...], Kind]] = [
    # not food first: "paper towels" must not become "towel" food, "dish soap" not "dish"
    (("paper towel", "paper towels", "toilet paper", "bath tissue", "dish soap", "detergent", "trash bag",
      "trash bags", "garbage bag", "sponge", "sponges", "aluminum foil", "foil", "plastic wrap", "napkins",
      "tissues", "shampoo", "conditioner", "toothpaste", "toothbrush", "deodorant", "body wash", "hand soap",
      "bleach", "cleaner", "disinfecting", "wipes", "batteries", "light bulb", "zip bags", "storage bags",
      "dishwasher", "laundry", "fabric softener", "dryer sheets", "lotion", "razor", "cotton", "pet food",
      "cat litter", "candles"), Kind("household", "none", None)),
    # frozen
    (("ice cream", "gelato", "frozen yogurt", "popsicle"), Kind("frozen", "freezer", "ice_cream")),
    (("dumpling", "dumplings", "wonton", "wontons", "potsticker", "potstickers", "gyoza", "mandu"),
     Kind("frozen", "freezer", "dumplings")),
    (("frozen pizza", "pizza"), Kind("frozen", "freezer", "pizza")),
    (("frozen",), Kind("frozen", "freezer", "frozen_bag")),
    # dairy, eggs, plant milks
    (("peanut butter", "almond butter", "nut butter"), Kind("condiments", "pantry", "sauce_jar")),
    (("oat milk", "almond milk", "soy milk", "plant milk", "oatmilk", "coconut milk beverage"),
     Kind("drinks", "fridge", "plant_milk")),
    (("egg", "eggs"), Kind("eggs", "fridge", "eggs")),
    (("yogurt", "yoghurt", "skyr", "kefir"), Kind("dairy", "fridge", "yogurt")),
    (("butter",), Kind("dairy", "fridge", "butter")),  # "peanut butter" is matched earlier below
    (("cheese", "cheddar", "mozzarella", "parmesan", "feta", "brie", "provolone", "ricotta", "cream cheese"),
     Kind("dairy", "fridge", "cheese")),
    (("milk", "half and half", "half & half", "creamer", "heavy cream", "sour cream"), Kind("dairy", "fridge", "milk")),
    # drinks
    (("orange juice", "apple juice", "juice", "lemonade"), Kind("drinks", "fridge", "juice")),
    (("sparkling water", "seltzer", "water", "spring water"), Kind("drinks", "pantry", "water")),
    (("cola", "soda", "ginger ale", "root beer", "sprite"), Kind("drinks", "pantry", "soda")),
    (("coffee", "espresso", "cold brew"), Kind("coffee_tea", "pantry", "coffee")),
    (("tea", "matcha", "chai"), Kind("coffee_tea", "pantry", "tea")),
    # grains and bakery
    (("rice",), Kind("grains", "pantry", "rice")),
    (("ramen", "ramyun", "noodle", "noodles", "udon", "soba", "lo mein"), Kind("grains", "pantry", "noodles")),
    (("spaghetti", "pasta", "penne", "macaroni", "fusilli", "linguine", "rigatoni"), Kind("grains", "pantry", "pasta")),
    (("cereal", "granola", "cheerios"), Kind("grains", "pantry", "cereal")),
    (("oats", "oatmeal"), Kind("grains", "pantry", "oats")),
    (("flour", "sugar", "baking"), Kind("grains", "pantry", "flour")),
    (("tortilla chips", "chips", "crisps", "pretzels", "popcorn"), Kind("snacks", "pantry", "chips")),
    (("tortilla", "tortillas", "wraps", "pita"), Kind("bakery", "pantry", "tortillas")),
    (("bread", "bagel", "bagels", "buns", "rolls", "baguette", "croissant", "muffins", "sourdough"),
     Kind("bakery", "pantry", "bread")),
    # snacks
    (("cookie", "cookies", "biscuits", "choco pie"), Kind("snacks", "pantry", "cookies")),
    (("protein bar", "protein bars", "granola bar", "bar", "bars"), Kind("snacks", "pantry", "bar")),
    (("chocolate", "candy"), Kind("snacks", "pantry", "chocolate")),
    (("almonds", "cashews", "walnuts", "pecans", "peanuts", "pistachios", "nuts", "trail mix"),
     Kind("snacks", "pantry", "nuts")),
    (("crackers", "seaweed", "snack"), Kind("snacks", "pantry", None)),
    # meat and fish
    (("chicken", "turkey", "wings", "drumsticks"), Kind("meat_fish", "fridge", "chicken")),
    (("beef", "steak", "ground chuck", "brisket", "lamb", "pork", "bacon", "sausage", "ham", "pork belly"),
     Kind("meat_fish", "fridge", "beef")),
    (("salmon", "tuna steak", "cod", "tilapia", "shrimp", "fish", "fillet", "scallops"),
     Kind("meat_fish", "fridge", "fish")),
    (("tofu", "tempeh", "seitan"), Kind("plant_protein", "fridge", "tofu")),
    # prepared
    (("kimchi", "pickles", "sauerkraut"), Kind("prepared", "fridge", "kimchi_jar")),
    (("hummus", "guacamole", "salsa fresca", "rotisserie", "deli salad", "prepared"),
     Kind("prepared", "fridge", "leftovers")),
    # condiments before produce ("peanut butter", "tomato sauce", "hot pepper paste")
    (("peanut butter", "almond butter", "nut butter", "jam", "jelly", "honey", "marinara", "pasta sauce",
      "tomato sauce", "salsa", "pesto"), Kind("condiments", "pantry", "sauce_jar")),
    (("olive oil", "vegetable oil", "canola oil", "sesame oil", "oil", "vinegar"), Kind("condiments", "pantry", "oil")),
    (("ketchup", "mustard", "mayo", "mayonnaise", "soy sauce", "hot sauce", "sriracha", "gochujang", "dressing",
      "paste", "syrup"), Kind("condiments", "pantry", "condiment")),
    (("salt", "pepper ground", "spice", "spices", "cinnamon", "paprika", "cumin", "seasoning"),
     Kind("condiments", "pantry", "spices")),
    # canned
    (("black beans", "kidney beans", "chickpeas", "pinto beans", "beans"), Kind("canned", "pantry", "beans")),
    (("diced tomatoes", "crushed tomatoes", "canned", "can", "soup", "tuna", "corn can"),
     Kind("canned", "pantry", "canned")),
    # produce
    (("banana", "bananas", "plantain"), Kind("produce", "pantry", "banana")),
    (("apple", "apples", "pear", "pears"), Kind("produce", "fridge", "apple")),
    (("strawberries", "strawberry", "blueberries", "raspberries", "blackberries", "berries", "grapes", "cherries"),
     Kind("produce", "fridge", "berries")),
    (("lime", "limes", "lemon", "lemons", "orange", "oranges", "mandarin", "clementines", "grapefruit"),
     Kind("produce", "fridge", "citrus")),
    (("spinach", "lettuce", "kale", "arugula", "greens", "romaine", "spring mix", "cabbage", "bok choy"),
     Kind("produce", "fridge", "leafy_greens")),
    (("carrot", "carrots", "potato", "potatoes", "sweet potato", "beets", "radish", "ginger"),
     Kind("produce", "fridge", "root_veg")),
    (("tomato", "tomatoes"), Kind("produce", "pantry", "tomato")),
    (("onion", "onions", "garlic", "shallot", "shallots", "scallions"), Kind("produce", "pantry", "onion_garlic")),
    (("cilantro", "parsley", "basil", "mint", "dill", "herbs"), Kind("produce", "fridge", "herbs")),
    (("avocado", "avocados", "mango", "peach", "melon", "pineapple"), Kind("produce", "pantry", None)),
    (("broccoli", "cucumber", "zucchini", "bell pepper", "peppers", "mushrooms", "celery", "asparagus", "cauliflower"),
     Kind("produce", "fridge", None)),
]  # fmt: skip

_COMPILED = [
    (tuple(re.compile(r"\b" + r"\s+".join(map(re.escape, p.split())) + r"\b") for p in phrases), kind)
    for phrases, kind in _RULES
]

DEFAULT = Kind("other", "pantry", None)


@dataclass(frozen=True, slots=True)
class Classification:
    category: str
    storage: str
    icon_key: str
    shelf_life_days: int | None


def classify(name: str) -> Classification:
    text = re.sub(r"[^a-z0-9&]+", " ", name.lower())
    kind = DEFAULT
    for patterns, k in _COMPILED:
        if any(p.search(text) for p in patterns):
            kind = k
            break
    storage = storage_for(kind.category, kind.storage)
    icon = kind.icon if kind.icon in ICONS else generic_icon(storage)
    shelf = None if storage == "none" else default_shelf_life(kind.category, kind.icon)
    return Classification(category=kind.category, storage=storage, icon_key=icon, shelf_life_days=shelf)
