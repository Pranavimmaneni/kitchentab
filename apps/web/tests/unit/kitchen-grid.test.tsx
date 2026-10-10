import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FoodIcon } from "../../components/FoodIcon";
import { AreaGrid, ItemTile } from "../../components/ItemTile";
import { VIRTUALIZE_AT, chunk, columnsFor, visibleRows } from "../../lib/area-grid";
import { FOOD_ICONS, FOOD_ICON_KEYS, resolveFoodIcon } from "../../lib/icons/food";
import { ME, item, owner } from "./fixtures/kitchen";

/** The v1 icon list in PRD §19.8 (= apps/api/app/extraction/classify.py ICONS). */
const PRD_ICONS =
  "milk plant_milk eggs yogurt cheese butter juice soda water bread tortillas cereal oats rice pasta noodles flour oil sauce_jar condiment spices canned beans chips cookies bar chocolate nuts coffee tea banana apple berries citrus leafy_greens root_veg tomato onion_garlic herbs chicken beef fish tofu kimchi_jar leftovers frozen_bag ice_cream dumplings pizza generic_fridge generic_pantry".split(
    " ",
  );

describe("fridge and pantry tiles", () => {
  it("VIS-04 every icon_key has a food icon of at most three paths on a pastel, filled only with tokens [FR-67]", () => {
    expect([...FOOD_ICON_KEYS].sort()).toEqual([...PRD_ICONS].sort());
    const tokens = readFileSync(resolve(process.cwd(), "styles/tokens.css"), "utf8");
    for (const key of PRD_ICONS) {
      const icon = FOOD_ICONS[key as keyof typeof FOOD_ICONS];
      expect(icon.paths.length, key).toBeLessThanOrEqual(3);
      expect(tokens, `${key} pastel`).toContain(`--food-${icon.bg}:`);
      for (const [, fill] of icon.paths) {
        if (fill !== "none") expect(tokens, `${key} fill ${fill}`).toContain(`--ill-${fill}:`);
      }
      const { container } = render(<FoodIcon iconKey={key} />);
      const svg = container.querySelector("svg")!;
      expect(svg.getAttribute("stroke")).toBe("currentColor");
      expect(svg.getAttribute("aria-hidden")).toBe("true");
      expect(svg.getAttribute("data-food-icon")).toBe(key);
    }
    expect(resolveFoodIcon("mystery", "pantry")).toBe("generic_pantry");
    expect(resolveFoodIcon(null, "freezer")).toBe("generic_fridge");
  });

  it("KITCH-07 a filtered-out tile dims instead of disappearing and keeps its label [FR-66] [FR-67]", () => {
    const onOpen = vi.fn();
    const spinach = item({
      id: "s",
      name: "Baby spinach",
      short_name: "Baby spinach",
      use_soon: true,
      owners: [owner("pranav"), owner("meera")],
    });
    render(<ItemTile item={spinach} meMemberId={ME} dimmed onOpen={onOpen} />);
    const tile = screen.getByRole("button", {
      name: "Baby spinach, owned by you & Meera, ask first, use soon",
    });
    expect(tile.getAttribute("data-dim")).toBe("true");
    expect(tile.textContent).toContain("SOON");
    fireEvent.click(tile);
    expect(onOpen).toHaveBeenCalledWith(spinach);
  });

  it("KITCH-16 row math: five tiles a row at 390 px and a window of rows around the viewport [FR-65]", () => {
    expect(columnsFor(310)).toBe(5);
    expect(columnsFor(400)).toBe(6);
    expect(columnsFor(0)).toBe(5);
    expect(chunk([1, 2, 3, 4, 5, 6, 7], 5)).toEqual([
      [1, 2, 3, 4, 5],
      [6, 7],
    ]);
    // Area top 300 px below the viewport top, 844 px viewport, 20 rows of 84 px.
    expect(visibleRows(300, 844, 20)).toEqual({ start: 0, end: 11 });
    // Scrolled so the area top is 1000 px above the viewport.
    expect(visibleRows(-1000, 844, 20)).toEqual({ start: 7, end: 20 });
    expect(visibleRows(5000, 844, 20)).toEqual({ start: 0, end: 0 });
  });

  it("KITCH-16 EDGE-19-10 an area with 60+ items renders only nearby rows but keeps its full height [FR-65]", () => {
    const many = Array.from({ length: 200 }, (_, i) =>
      item({ id: `i${i}`, name: `Leftovers ${i}`, short_name: `L${i}` }),
    );
    const { container } = render(
      <AreaGrid
        items={many}
        filter="all"
        meMemberId={ME}
        label="refrigerated, 200 items"
        onOpen={() => undefined}
      />,
    );
    const area = screen.getByRole("group", { name: "refrigerated, 200 items" });
    expect(area.getAttribute("data-virtual")).toBe("true");
    expect(area.style.height).toBe(`${Math.ceil(200 / 5) * 84}px`);
    const rendered = container.querySelectorAll(".kt-tile").length;
    expect(rendered).toBeGreaterThan(0);
    expect(rendered).toBeLessThan(200);

    const few = many.slice(0, VIRTUALIZE_AT);
    const small = render(
      <AreaGrid items={few} filter="mine" meMemberId={ME} label="few" onOpen={() => undefined} />,
    );
    expect(small.container.querySelectorAll(".kt-tile")).toHaveLength(VIRTUALIZE_AT);
  });
});
