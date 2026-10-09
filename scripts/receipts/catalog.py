"""Product catalog for synthetic receipts (scripts/receipts/generate.py).

Each product: truth name (what a person reading the receipt would type), the abbreviated caps form
a thermal register prints, a long e-receipt form, the human label (category, storage, icon) and a
base price in cents. `per_lb` products are sold by weight. Thermal abbreviations are ordinary
grocery shorthand; the parser's dictionary is generic and does not read this file.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    key: str
    name: str  # truth name, generic (no store brand)
    abbr: str  # thermal caps form
    long: str  # e-receipt form (Instacart / Amazon)
    category: str
    storage: str
    icon: str
    price: int  # cents per unit, or per lb when per_lb
    per_lb: bool = False


P = Product
CATALOG: list[Product] = [
    # produce
    P("bananas", "Organic bananas", "ORG BNNA", "Organic Bananas", "produce", "pantry", "banana", 63, per_lb=True),
    P("apples", "Honeycrisp apples", "HONEYCRISP APPLES", "Honeycrisp Apples", "produce", "fridge", "apple", 249, per_lb=True),
    P("tomatoes", "Tomatoes on vine", "TOMATOES ON VINE", "Tomatoes on the Vine", "produce", "pantry", "tomato", 299, per_lb=True),
    P("onions", "Yellow onions", "YELLOW ONIONS", "Yellow Onions", "produce", "pantry", "onion_garlic", 129, per_lb=True),
    P("grapes", "Red seedless grapes", "RED SDLS GRAPES", "Red Seedless Grapes", "produce", "fridge", "berries", 299, per_lb=True),
    P("spinach", "Baby spinach", "BBY SPINACH", "Organic Baby Spinach, 5 oz", "produce", "fridge", "leafy_greens", 399),
    P("strawberries", "Strawberries 1 lb", "STRAWBERRIES 1LB", "Strawberries, 1 lb", "produce", "fridge", "berries", 449),
    P("blueberries", "Blueberries pint", "BLUEBERRIES PINT", "Blueberries, 1 pint", "produce", "fridge", "berries", 399),
    P("avocado", "Hass avocado", "HASS AVOCADO", "Hass Avocado, Large", "produce", "pantry", "generic_pantry", 129),
    P("limes", "Limes", "LIMES", "Limes", "produce", "fridge", "citrus", 50),
    P("garlic", "Garlic", "GARLIC", "Garlic Bulb", "produce", "pantry", "onion_garlic", 79),
    P("cilantro", "Cilantro", "CILANTRO", "Cilantro Bunch", "produce", "fridge", "herbs", 99),
    P("carrots", "Carrots 2 lb", "CARROTS 2LB", "Carrots, 2 lb Bag", "produce", "fridge", "root_veg", 229),
    # dairy, eggs
    P("milk", "Whole milk 1 gal", "WHOLE MILK 1 GAL", "Whole Milk, 1 Gallon", "dairy", "fridge", "milk", 429),
    P("yogurt", "Greek yogurt plain 32 oz", "GRK YGRT PLN 32OZ", "Greek Yogurt Plain, 32 oz", "dairy", "fridge", "yogurt", 679),
    P("eggs", "Large eggs 18 ct", "LG EGGS 18CT", "Large Eggs, 18 ct", "eggs", "fridge", "eggs", 549),
    P("cheddar", "Shredded cheddar cheese 8 oz", "SHRD CHED CHS 8OZ", "Shredded Cheddar Cheese, 8 oz", "dairy", "fridge", "cheese", 349),
    P("butter", "Unsalted butter 1 lb", "UNSLT BUTTER 1LB", "Unsalted Butter, 1 lb", "dairy", "fridge", "butter", 499),
    P("mozz", "Fresh mozzarella 8 oz", "FRESH MOZZ 8OZ", "Fresh Mozzarella, 8 oz", "dairy", "fridge", "cheese", 449),
    P("oatmilk", "Oat milk 64 oz", "OAT MILK 64OZ", "Oat Milk Original, 64 fl oz", "drinks", "fridge", "plant_milk", 459),
    P("oj", "Orange juice 52 oz", "OJ 52OZ", "Orange Juice No Pulp, 52 fl oz", "drinks", "fridge", "juice", 399),
    # meat, fish, protein
    P("thighs", "Boneless chicken thighs", "BNLS CHKN THIGHS", "Boneless Skinless Chicken Thighs", "meat_fish", "fridge", "chicken", 379, per_lb=True),
    P("beef", "Ground beef 80/20", "GRND BEEF 80/20", "Ground Beef 80% Lean", "meat_fish", "fridge", "beef", 599, per_lb=True),
    P("salmon", "Atlantic salmon fillet", "ATL SALMON FILLET", "Atlantic Salmon Fillet", "meat_fish", "fridge", "fish", 1199, per_lb=True),
    P("porkbelly", "Pork belly sliced", "PORK BELLY SLICED", "Pork Belly, Sliced", "meat_fish", "fridge", "generic_fridge", 899, per_lb=True),
    P("tofu", "Firm tofu 14 oz", "FIRM TOFU 14OZ", "Organic Firm Tofu, 14 oz", "plant_protein", "fridge", "tofu", 249),
    P("hummus", "Hummus 10 oz", "HUMMUS 10OZ", "Classic Hummus, 10 oz", "prepared", "fridge", "leftovers", 399),
    # pantry
    P("rice", "Basmati rice 10 lb", "BASMATI RICE 10LB", "Basmati Rice, 10 lb", "grains", "pantry", "rice", 1499),
    P("pasta", "Spaghetti 16 oz", "SPAGHETTI 16OZ", "Spaghetti Pasta, 16 oz", "grains", "pantry", "pasta", 149),
    P("ramen", "Shin ramyun 4 pk", "SHIN RAMYUN 4PK", "Shin Ramyun Noodle Soup, 4 Pack", "grains", "pantry", "noodles", 599),
    P("cereal", "Honey nut cereal", "HONEY NUT CEREAL", "Honey Nut Cereal, 15.4 oz", "grains", "pantry", "cereal", 449),
    P("oats", "Rolled oats 42 oz", "ROLLED OATS 42OZ", "Old Fashioned Rolled Oats, 42 oz", "grains", "pantry", "oats", 529),
    P("bread", "Whole wheat bread", "WHL WHT BREAD", "Whole Wheat Sandwich Bread", "bakery", "pantry", "bread", 349),
    P("tortillas", "Flour tortillas 10 ct", "FLOUR TORTILLAS 10CT", "Flour Tortillas, 10 ct", "bakery", "pantry", "tortillas", 299),
    P("pb", "Creamy peanut butter", "CRMY PNT BUTTER", "Creamy Peanut Butter, 16 oz", "condiments", "pantry", "sauce_jar", 279),
    P("marinara", "Marinara sauce 24 oz", "MARINARA SAUCE 24OZ", "Marinara Sauce, 24 oz", "condiments", "pantry", "sauce_jar", 349),
    P("oliveoil", "Extra virgin olive oil 500 ml", "EVOO 500ML", "Extra Virgin Olive Oil, 500 ml", "condiments", "pantry", "oil", 899),
    P("beans", "Black beans 15 oz", "BLACK BEANS 15OZ", "Black Beans, 15 oz Can", "canned", "pantry", "beans", 119),
    P("tomcan", "Diced tomatoes 28 oz", "DICED TOMATOES 28OZ", "Diced Tomatoes, 28 oz Can", "canned", "pantry", "canned", 199),
    P("coffee", "Coffee beans 12 oz", "COFFEE BEANS 12OZ", "Whole Bean Coffee, Medium Roast, 12 oz", "coffee_tea", "pantry", "coffee", 999),
    P("tea", "Green tea 20 ct", "GREEN TEA 20CT", "Green Tea, 20 Bags", "coffee_tea", "pantry", "tea", 349),
    P("chips", "Tortilla chips", "TORTILLA CHIPS", "Tortilla Chips, 13 oz", "snacks", "pantry", "chips", 429),
    P("bars", "Protein bars 12 ct", "PROTEIN BARS 12CT", "Protein Bars Chocolate, 12 ct", "snacks", "pantry", "bar", 1799),
    P("almonds", "Roasted almonds 16 oz", "RSTD ALMONDS 16OZ", "Roasted Almonds, 16 oz", "snacks", "pantry", "nuts", 699),
    P("cookies", "Chocolate chip cookies", "CHOC CHIP COOKIES", "Chocolate Chip Cookies, 13 oz", "snacks", "pantry", "cookies", 399),
    P("seltzer", "Sparkling water 12 pk", "SPRKLNG WATER 12PK", "Sparkling Water Lime, 12 pk", "drinks", "pantry", "water", 549),
    P("cola", "Cola 2 liter", "COLA 2 LITER", "Cola, 2 Liter Bottle", "drinks", "pantry", "soda", 249),
    P("kimchi", "Napa kimchi 32 oz", "NAPA KIMCHI 32OZ", "Napa Cabbage Kimchi, 32 oz", "prepared", "fridge", "kimchi_jar", 899),
    P("gochujang", "Gochujang paste", "GOCHUJANG PASTE", "Gochujang Hot Pepper Paste, 17.6 oz", "condiments", "pantry", "condiment", 699),
    # frozen
    P("dumplings", "Frozen pork dumplings", "FRZ PORK DUMPLINGS", "Pork & Vegetable Dumplings, Frozen", "frozen", "freezer", "dumplings", 899),
    P("icecream", "Vanilla ice cream", "VANILLA ICE CREAM", "Vanilla Bean Ice Cream, 1.5 qt", "frozen", "freezer", "ice_cream", 549),
    P("pizza", "Frozen pepperoni pizza", "FRZ PEPPERONI PIZZA", "Pepperoni Pizza, Frozen", "frozen", "freezer", "pizza", 699),
    P("peas", "Frozen peas 16 oz", "FRZ PEAS 16OZ", "Sweet Peas, Frozen, 16 oz", "frozen", "freezer", "frozen_bag", 199),
    P("orangechkn", "Mandarin orange chicken", "MANDARIN ORANGE CHKN", "Mandarin Orange Chicken, Frozen", "frozen", "freezer", "frozen_bag", 499),
    # household
    P("towels", "Paper towels 6 pk", "PAPER TOWELS 6PK", "Paper Towels, 6 Double Rolls", "household", "none", "generic_pantry", 999),
    P("dishsoap", "Dish soap", "DISH SOAP", "Dish Soap Original, 19.4 fl oz", "household", "none", "generic_pantry", 349),
    P("tp", "Toilet paper 12 roll", "TOILET PAPER 12 ROLL", "Toilet Paper, 12 Mega Rolls", "household", "none", "generic_pantry", 1299),
    P("detergent", "Laundry detergent", "LNDRY DETERGENT", "Laundry Detergent, 64 loads", "household", "none", "generic_pantry", 1199),
    P("trashbags", "Trash bags 40 ct", "TRASH BAGS 40CT", "Tall Kitchen Trash Bags, 40 ct", "household", "none", "generic_pantry", 899),
    P("sponges", "Sponges 3 pk", "SPONGES 3PK", "Non-Scratch Sponges, 3 pk", "household", "none", "generic_pantry", 299),
]  # fmt: skip

BY_KEY = {p.key: p for p in CATALOG}
