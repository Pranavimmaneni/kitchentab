/**
 * Food icon set (PRD §19.8): one entry per `icon_key` the extractor and leftovers can produce
 * (apps/api/app/extraction/classify.py ICONS). Each icon is a 24×24 viewBox with at most three
 * paths, stroked in the ink of the sticker look (the component sets `currentColor`) on one of the
 * five food pastels. The 15 icons drawn in Fridge.dc.html are copied exactly; the rest follow the
 * same style. Fills are illustration tokens from styles/tokens.css.
 */

export type Pastel = "sky" | "butter" | "lilac" | "peach" | "pea";

/** A fill token name (`--ill-*`) or "none". */
export type Fill =
  | "none"
  | "white"
  | "cream"
  | "stone"
  | "tan"
  | "sky"
  | "lime"
  | "coral"
  | "red"
  | "caramel"
  | "orange"
  | "yellow"
  | "leaf"
  | "violet"
  | "lavender"
  | "pink"
  | "magenta"
  | "brown"
  | "light-brown"
  | "dark-brown"
  | "grey"
  | "note";

export type FoodIconData = { bg: Pastel; paths: readonly (readonly [d: string, fill: Fill])[] };

const CARTON = "M7.5 9.5 L7.5 20.5 L16.5 20.5 L16.5 9.5 L14 6 L10 6 Z";
const CARTON_CAP = "M10 6 L10 3.5 L14 3.5 L14 6";

export const FOOD_ICONS = {
  // ---- drawn in Fridge.dc.html (copied exactly) ----
  milk: {
    bg: "sky",
    paths: [
      [CARTON, "white"],
      [CARTON_CAP, "none"],
      ["M7.5 13 H16.5 V17 H7.5 Z", "sky"],
    ],
  },
  plant_milk: {
    bg: "butter",
    paths: [
      [CARTON, "white"],
      [CARTON_CAP, "none"],
      ["M7.5 13 H16.5 V17 H7.5 Z", "tan"],
    ],
  },
  eggs: {
    bg: "butter",
    paths: [
      ["M3.5 13.5 H20.5 V17.5 Q20.5 19.5 18.5 19.5 H5.5 Q3.5 19.5 3.5 17.5 Z", "tan"],
      ["M6 13.5 C6 8.5 11 8.5 11 13.5", "white"],
      ["M13 13.5 C13 8.5 18 8.5 18 13.5", "white"],
    ],
  },
  yogurt: {
    bg: "lilac",
    paths: [
      ["M6.5 8.5 L8.2 20 H15.8 L17.5 8.5 Z", "white"],
      ["M5.5 6 H18.5 V8.5 H5.5 Z", "lime"],
      ["M7.3 12 H16.7 L16.2 15.5 H7.8 Z", "coral"],
    ],
  },
  chicken: {
    bg: "peach",
    paths: [
      [
        "M13 4 C18 3 21.5 7.5 19.5 11.5 C18 14.5 14.5 15.5 12 14.5 L9.5 12 C8.5 9 9.5 4.8 13 4 Z",
        "caramel",
      ],
      ["M10.5 13.5 L6.8 17.2", "none"],
      ["M4.2 17.6 a1.8 1.8 0 1 0 3.6 0 a1.8 1.8 0 1 0 -3.6 0", "white"],
    ],
  },
  leafy_greens: {
    bg: "pea",
    paths: [
      ["M5 19.5 C5 10.5 11 4.5 19.5 4.5 C20 13 14 19.5 5 19.5 Z", "leaf"],
      ["M5 19.5 L15 9.5", "none"],
    ],
  },
  kimchi_jar: {
    bg: "peach",
    paths: [
      ["M7 7.5 H17 V19.5 Q17 20.5 16 20.5 H8 Q7 20.5 7 19.5 Z", "red"],
      ["M6.5 4.5 H17.5 V7.5 H6.5 Z", "violet"],
      ["M9 11.5 H15 V16 H9 Z", "white"],
    ],
  },
  tofu: {
    bg: "sky",
    paths: [
      ["M4 9.5 L12 5.5 L20 9.5 L12 13.5 Z", "white"],
      ["M4 9.5 V15.5 L12 19.5 V13.5 Z", "cream"],
      ["M20 9.5 V15.5 L12 19.5 V13.5 Z", "stone"],
    ],
  },
  dumplings: {
    bg: "lilac",
    paths: [
      ["M6 5 H18 L19 20.5 H5 Z", "sky"],
      ["M8 15.5 C8 11 16 11 16 15.5 Z", "white"],
      ["M10 13 L10.6 14.6 M12 12.4 V14.4 M14 13 L13.4 14.6", "none"],
    ],
  },
  ice_cream: {
    bg: "peach",
    paths: [
      ["M6 9.5 L7.5 20.5 H16.5 L18 9.5 Z", "pink"],
      ["M5 7 H19 V9.5 H5 Z", "white"],
      ["M8 13 H16", "none"],
    ],
  },
  rice: {
    bg: "peach",
    paths: [
      ["M6 7.5 C6 5 8.5 4 12 4 C15.5 4 18 5 18 7.5 L19 20.5 H5 Z", "white"],
      ["M8 7.5 H16", "none"],
      ["M8.2 11.5 H15.8 V16 H8.2 Z", "magenta"],
    ],
  },
  banana: {
    bg: "butter",
    paths: [
      ["M4.5 7.5 C5.5 16 12.5 20.5 20 15 C19 14 17.8 14 17 14.6 C12 17.5 8 14 6.3 7.5 Z", "yellow"],
      ["M4.5 7.5 L4 5.5 L6 5.3 L6.3 7.5", "grey"],
    ],
  },
  bar: {
    bg: "lilac",
    paths: [
      ["M3 9 H21 V17 H3 Z", "lavender"],
      ["M3 12.2 H21", "none"],
      ["M8 14.6 H16", "none"],
    ],
  },
  coffee: {
    bg: "butter",
    paths: [
      ["M7 6.5 L6 20.5 H18 L17 6.5 Z", "brown"],
      ["M7 6.5 L9 3.5 H15 L17 6.5", "light-brown"],
      ["M10.4 13.5 a1.6 2.2 0 1 0 3.2 0 a1.6 2.2 0 1 0 -3.2 0 M12 11.3 V15.7", "dark-brown"],
    ],
  },
  noodles: {
    bg: "peach",
    paths: [
      ["M4 7.5 H20 V19.5 H4 Z", "orange"],
      ["M4 7.5 L6 5.5 L8 7.5 L10 5.5 L12 7.5 L14 5.5 L16 7.5 L18 5.5 L20 7.5", "none"],
      [
        "M7 12 C9 10.5 10 13.5 12 12 C14 10.5 15 13.5 17 12 M7 15 C9 13.5 10 16.5 12 15 C14 13.5 15 16.5 17 15",
        "none",
      ],
    ],
  },

  // ---- the rest of the v1 set, drawn in the same style ----
  cheese: {
    bg: "butter",
    paths: [
      ["M3.5 12 H20.5 V18.5 H3.5 Z", "yellow"],
      ["M3.5 12 L15.5 6 L20.5 12 Z", "note"],
      [
        "M7 15 a1.2 1.2 0 1 0 2.4 0 a1.2 1.2 0 1 0 -2.4 0 M13.5 15.8 a1 1 0 1 0 2 0 a1 1 0 1 0 -2 0",
        "tan",
      ],
    ],
  },
  butter: {
    bg: "butter",
    paths: [
      ["M3.5 11 H17.5 V17 H3.5 Z", "yellow"],
      ["M3.5 11 L6.5 8 H20.5 L17.5 11 Z", "note"],
      ["M17.5 11 L20.5 8 V14 L17.5 17 Z", "tan"],
    ],
  },
  juice: {
    bg: "peach",
    paths: [
      [CARTON, "white"],
      ["M13 6 L15 2.5", "none"],
      ["M9.5 14.5 a2.5 2.5 0 1 0 5 0 a2.5 2.5 0 1 0 -5 0", "orange"],
    ],
  },
  soda: {
    bg: "lilac",
    paths: [
      ["M7.5 5.5 H16.5 V19.5 Q16.5 20.5 15.5 20.5 H8.5 Q7.5 20.5 7.5 19.5 Z", "red"],
      ["M8.5 3.5 H15.5 L16.5 5.5 H7.5 Z", "stone"],
      ["M7.5 10 H16.5 V14 H7.5 Z", "white"],
    ],
  },
  water: {
    bg: "sky",
    paths: [
      [
        "M9 8 C9 7 10 6.5 10 5.5 V4 H14 V5.5 C14 6.5 15 7 15 8 V19.5 Q15 20.5 14 20.5 H10 Q9 20.5 9 19.5 Z",
        "white",
      ],
      ["M9 11 H15 V15 H9 Z", "sky"],
      ["M9.8 2.5 H14.2 V4 H9.8 Z", "violet"],
    ],
  },
  bread: {
    bg: "butter",
    paths: [
      [
        "M4 12 C4 7 20 7 20 12 C20 13.4 19.2 14 18.5 14 V19.5 H5.5 V14 C4.8 14 4 13.4 4 12 Z",
        "light-brown",
      ],
      ["M9 9.5 L10.5 11.5 M13 9 L14.5 11", "none"],
      ["M5.5 16.5 H18.5", "none"],
    ],
  },
  tortillas: {
    bg: "butter",
    paths: [
      ["M3.5 14.5 a8.5 3.5 0 1 0 17 0 a8.5 3.5 0 1 0 -17 0", "tan"],
      ["M3.5 11 a8.5 3.5 0 1 0 17 0 a8.5 3.5 0 1 0 -17 0", "note"],
      ["M9 10.5 h0.01 M14 11.5 h0.01 M11.5 12 h0.01", "none"],
    ],
  },
  cereal: {
    bg: "peach",
    paths: [
      ["M6 4 H18 V20.5 H6 Z", "orange"],
      ["M8 12 H16 C16 15 14 16.5 12 16.5 C10 16.5 8 15 8 12 Z", "white"],
      ["M9.5 10 h1 M12 9.5 h1 M14 10 h0.8", "none"],
    ],
  },
  oats: {
    bg: "butter",
    paths: [
      ["M6.5 6.5 H17.5 V19.5 Q17.5 20.5 16.5 20.5 H7.5 Q6.5 20.5 6.5 19.5 Z", "tan"],
      ["M6 4 H18 V6.5 H6 Z", "red"],
      ["M9 11 H15 V16 H9 Z", "white"],
    ],
  },
  pasta: {
    bg: "pea",
    paths: [
      ["M4 7.5 L12 12 L4 16.5 Z", "yellow"],
      ["M20 7.5 L12 12 L20 16.5 Z", "yellow"],
      ["M10.5 10.5 H13.5 V13.5 H10.5 Z", "orange"],
    ],
  },
  flour: {
    bg: "sky",
    paths: [
      ["M6 7 L8 4.5 H16 L18 7 L18.5 20.5 H5.5 Z", "white"],
      ["M5.8 11 H18.2 V15 H5.8 Z", "stone"],
      ["M9 4.5 L10 7 M15 4.5 L14 7", "none"],
    ],
  },
  oil: {
    bg: "pea",
    paths: [
      ["M9.5 9 L10.5 6.5 V4.5 H13.5 V6.5 L14.5 9 V20.5 H9.5 Z", "yellow"],
      ["M9.5 13 H14.5 V17 H9.5 Z", "white"],
      ["M10.2 2.5 H13.8 V4.5 H10.2 Z", "leaf"],
    ],
  },
  sauce_jar: {
    bg: "peach",
    paths: [
      ["M6.5 8 H17.5 V19 Q17.5 20.5 16 20.5 H8 Q6.5 20.5 6.5 19 Z", "red"],
      ["M7 4.5 H17 V8 H7 Z", "stone"],
      ["M9 12 H15 V16.5 H9 Z", "white"],
    ],
  },
  condiment: {
    bg: "butter",
    paths: [
      ["M8 9 H16 V19.5 Q16 20.5 15 20.5 H9 Q8 20.5 8 19.5 Z", "red"],
      ["M10 9 L11 5 H13 L14 9 Z", "white"],
      ["M11.5 5 L12 3 L12.5 5", "none"],
    ],
  },
  spices: {
    bg: "lilac",
    paths: [
      ["M8 9 H16 V20.5 H8 Z", "orange"],
      ["M8.5 5 H15.5 V9 H8.5 Z", "white"],
      ["M10.5 7 h0.01 M12 7 h0.01 M13.5 7 h0.01", "none"],
    ],
  },
  canned: {
    bg: "sky",
    paths: [
      ["M6 7 V18 A6 1.8 0 0 0 18 18 V7 Z", "stone"],
      ["M6 10.5 H18 V15 H6 Z", "red"],
      ["M6 7 a6 1.8 0 1 0 12 0 a6 1.8 0 1 0 -12 0", "white"],
    ],
  },
  beans: {
    bg: "pea",
    paths: [
      ["M4.5 10 C4.5 6.5 9.5 6 10 9 C10.3 10.8 12.5 10.5 12.5 13 C12.5 16 4.5 15 4.5 10 Z", "red"],
      [
        "M11.5 15 C11.5 11.5 16.5 11 17 14 C17.3 15.8 19.5 15.5 19.5 18 C19.5 21 11.5 20 11.5 15 Z",
        "coral",
      ],
    ],
  },
  chips: {
    bg: "butter",
    paths: [
      [
        "M6 5 L7 4 L8 5 L9 4 L10 5 L11 4 L12 5 L13 4 L14 5 L15 4 L16 5 L17 4 L18 5 L17 20.5 H7 Z",
        "yellow",
      ],
      ["M9 10 C9 8 15 8 15 10 V14 C15 16 9 16 9 14 Z", "orange"],
    ],
  },
  cookies: {
    bg: "peach",
    paths: [
      ["M4.5 12 a7.5 7.5 0 1 0 15 0 a7.5 7.5 0 1 0 -15 0", "light-brown"],
      ["M9 9.5 h0.01 M14 10 h0.01 M11 14 h0.01 M15 14.5 h0.01 M8.5 13.5 h0.01", "none"],
    ],
  },
  chocolate: {
    bg: "lilac",
    paths: [
      ["M5 4.5 H19 V19.5 H5 Z", "brown"],
      ["M5 9.5 H19 M12 4.5 V12", "none"],
      ["M5 12 L19 15 V19.5 H5 Z", "red"],
    ],
  },
  nuts: {
    bg: "butter",
    paths: [
      [
        "M8 4.5 C11 4.5 12 7.5 11.5 9.5 C11 11 13 12.5 13 15 C13 18.5 10.5 20 8 20 C5 20 3.5 18 4 15 C4.5 12.5 6 11.5 5.5 9.5 C5 7 6 4.5 8 4.5 Z",
        "tan",
      ],
      ["M15.5 6 C19.5 7 21 12 18.5 15.5 C16 13.5 14.5 9 15.5 6 Z", "light-brown"],
      ["M6.5 8.5 h0.01 M8.5 14 h0.01 M7 17 h0.01", "none"],
    ],
  },
  tea: {
    bg: "pea",
    paths: [
      ["M5 8 H16 V17 Q16 20 13 20 H8 Q5 20 5 17 Z", "white"],
      ["M16 10 H17.5 Q19.5 10 19.5 12.5 Q19.5 15 17.5 15 H16", "none"],
      ["M10.5 8 V11.5 M9 11.5 H12 V14.5 H9 Z", "leaf"],
    ],
  },
  apple: {
    bg: "peach",
    paths: [
      [
        "M12 7.5 C9 5.5 4.5 6.5 4.5 11.5 C4.5 16.5 8 20.5 12 19.5 C16 20.5 19.5 16.5 19.5 11.5 C19.5 6.5 15 5.5 12 7.5 Z",
        "red",
      ],
      ["M12 7.5 C12 5.5 12.5 4.5 13.5 3.5", "none"],
      ["M13 5.5 C15 3.5 17.5 4 18 5 C16.5 6.5 14.5 6.5 13 5.5 Z", "leaf"],
    ],
  },
  berries: {
    bg: "lilac",
    paths: [
      ["M3.5 14 a3.5 3.5 0 1 0 7 0 a3.5 3.5 0 1 0 -7 0", "violet"],
      ["M13 15.5 a3.5 3.5 0 1 0 7 0 a3.5 3.5 0 1 0 -7 0", "magenta"],
      ["M8 8 a3.5 3.5 0 1 0 7 0 a3.5 3.5 0 1 0 -7 0", "red"],
    ],
  },
  citrus: {
    bg: "butter",
    paths: [
      ["M3.5 11 A8.5 8.5 0 0 0 20.5 11 Z", "orange"],
      ["M6 11 A6 6 0 0 0 18 11 Z", "note"],
      ["M12 11 V17 M12 11 L7.8 15.2 M12 11 L16.2 15.2", "none"],
    ],
  },
  root_veg: {
    bg: "peach",
    paths: [
      [
        "M14.5 7.5 C17 9 17.5 11.5 16 13 L6 20 C5 20.5 4 19.5 4.5 18.5 L11.5 8.5 C12.5 7 13.5 7 14.5 7.5 Z",
        "orange",
      ],
      ["M15 7 C15 4.5 16.5 3.5 18 3.5 M15.5 7.5 C17.5 6 19.5 6.5 20.5 7.5", "none"],
      ["M9 13 L10.5 14 M7.5 16 L9 17", "none"],
    ],
  },
  tomato: {
    bg: "peach",
    paths: [
      [
        "M4.5 13 C4.5 8.5 8 6.5 12 6.5 C16 6.5 19.5 8.5 19.5 13 C19.5 17.5 16 20 12 20 C8 20 4.5 17.5 4.5 13 Z",
        "red",
      ],
      ["M12 4 L13.2 6.6 L16 7 L13.5 8.3 L12 10 L10.5 8.3 L8 7 L10.8 6.6 Z", "leaf"],
    ],
  },
  onion_garlic: {
    bg: "butter",
    paths: [
      [
        "M12 3.5 C12 7 18.5 9 18.5 14 C18.5 18 15.5 20.5 12 20.5 C8.5 20.5 5.5 18 5.5 14 C5.5 9 12 7 12 3.5 Z",
        "white",
      ],
      ["M12 9 C10 12 10 17 12 20.5 M12 9 C14 12 14 17 12 20.5", "none"],
      ["M10 20.5 L9.5 21.5 M14 20.5 L14.5 21.5", "none"],
    ],
  },
  herbs: {
    bg: "pea",
    paths: [
      ["M12 21 V6", "none"],
      [
        "M12 10 C9 10 7 8 7 5.5 C9.5 5.5 12 7 12 10 Z M12 14 C15 14 17 12 17 9.5 C14.5 9.5 12 11 12 14 Z",
        "leaf",
      ],
      ["M12 18 C9 18 7 16 7 13.5 C9.5 13.5 12 15 12 18 Z", "leaf"],
    ],
  },
  beef: {
    bg: "peach",
    paths: [
      [
        "M5 9 C5 5.5 11 4 15 5.5 C19 7 20.5 11 19 14.5 C17.5 18 12 20 8 18.5 C5 17.5 4 14 5.5 12 C6 11 5 10.5 5 9 Z",
        "red",
      ],
      ["M15 5.5 C19 7 20.5 11 19 14.5 C18 13.5 17.5 11 16.5 9.5 C15.5 8 14.5 7 15 5.5 Z", "white"],
      ["M9.5 11.5 a1.8 1.8 0 1 0 3.6 0 a1.8 1.8 0 1 0 -3.6 0", "white"],
    ],
  },
  fish: {
    bg: "sky",
    paths: [
      ["M3.5 12 C6.5 7 13 6.5 17 12 C13 17.5 6.5 17 3.5 12 Z", "white"],
      ["M17 12 L20.5 8.5 V15.5 Z", "stone"],
      ["M7.5 11 h0.01", "none"],
    ],
  },
  leftovers: {
    bg: "lilac",
    paths: [
      ["M5 9 H19 L17.5 20.5 H6.5 Z", "white"],
      ["M5 9 L8 5.5 H16 L19 9 Z", "stone"],
      ["M9.5 5.5 C9.5 2.5 14.5 2.5 14.5 5.5", "none"],
    ],
  },
  frozen_bag: {
    bg: "sky",
    paths: [
      ["M6 5 H18 L19 20.5 H5 Z", "white"],
      ["M6 5 H18 V7.5 H6 Z", "sky"],
      ["M12 10 V17 M9 11.8 L15 15.2 M15 11.8 L9 15.2", "none"],
    ],
  },
  pizza: {
    bg: "butter",
    paths: [
      ["M4 6.5 C9 4 15 4 20 6.5 L12 21 Z", "yellow"],
      ["M4 6.5 C9 4 15 4 20 6.5 L19 8.3 C14.5 6 9.5 6 5 8.3 Z", "light-brown"],
      [
        "M10 11 a1.5 1.5 0 1 0 3 0 a1.5 1.5 0 1 0 -3 0 M12 15.2 a1.2 1.2 0 1 0 2.4 0 a1.2 1.2 0 1 0 -2.4 0",
        "red",
      ],
    ],
  },
  generic_fridge: {
    bg: "sky",
    paths: [
      ["M7 3.5 H17 V20.5 H7 Z", "white"],
      ["M7 9.5 H17", "none"],
      ["M9.5 5.5 V7.5 M9.5 12 V15", "none"],
    ],
  },
  generic_pantry: {
    bg: "butter",
    paths: [
      ["M4.5 10 H15.5 V20 H4.5 Z", "tan"],
      ["M4.5 10 L8 6.5 H19 L15.5 10 Z", "note"],
      ["M15.5 10 L19 6.5 V16.5 L15.5 20 Z", "light-brown"],
    ],
  },
} as const satisfies Record<string, FoodIconData>;

export type FoodIconKey = keyof typeof FOOD_ICONS;

export const FOOD_ICON_KEYS = Object.keys(FOOD_ICONS) as FoodIconKey[];

export function isFoodIconKey(key: string | null | undefined): key is FoodIconKey {
  return typeof key === "string" && Object.prototype.hasOwnProperty.call(FOOD_ICONS, key);
}

/** Unknown icon → the generic fridge or pantry icon by place (PRD §19.7 rules). */
export function resolveFoodIcon(
  key: string | null | undefined,
  place?: string | null,
): FoodIconKey {
  if (isFoodIconKey(key)) return key;
  return place === "pantry" ? "generic_pantry" : "generic_fridge";
}

export function fillVar(fill: Fill): string {
  return fill === "none" ? "none" : `var(--ill-${fill})`;
}

export function pastelVar(bg: Pastel): string {
  return `var(--food-${bg})`;
}
