import { FOOD_ICONS, fillVar, pastelVar, resolveFoodIcon } from "@/lib/icons/food";

export type FoodIconProps = {
  iconKey: string | null | undefined;
  /** Picks the generic fridge or pantry icon when the key is unknown. */
  place?: string | null;
  size?: number;
  strokeWidth?: number;
};

/**
 * One food icon (PRD §19.8): 24×24 viewBox, at most three paths, stroked with `currentColor` (the
 * container sets the sticker ink) and filled with illustration tokens. Always decorative: the tile
 * or row next to it carries the name.
 */
export function FoodIcon({ iconKey, place, size = 24, strokeWidth = 1.6 }: FoodIconProps) {
  const key = resolveFoodIcon(iconKey, place);
  const icon = FOOD_ICONS[key];
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinejoin="round"
      strokeLinecap="round"
      aria-hidden="true"
      focusable="false"
      data-food-icon={key}
    >
      {icon.paths.map(([d, fill], i) => (
        <path key={i} d={d} fill={fillVar(fill)} />
      ))}
    </svg>
  );
}

/** The pastel behind an icon, as a CSS value. */
export function foodPastel(iconKey: string | null | undefined, place?: string | null): string {
  return pastelVar(FOOD_ICONS[resolveFoodIcon(iconKey, place)].bg);
}
