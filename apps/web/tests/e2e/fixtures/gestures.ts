import { expect, type Locator, type Page } from "@playwright/test";

/**
 * One finger for the kitchen gesture journeys (TEST_PLAN 3.13). Chromium gets real touch through
 * the DevTools protocol, so native scrolling and touch-action are exercised (GEST-12). WebKit has
 * no CDP: it gets real pointer events from the mouse, which the gesture engine treats the same.
 */
export type Finger = {
  touch: boolean;
  down: (p: Point) => Promise<void>;
  move: (p: Point, steps?: number) => Promise<void>;
  up: () => Promise<void>;
};
export type Point = { x: number; y: number };

export async function finger(page: Page, browserName: string): Promise<Finger> {
  if (browserName === "chromium") {
    const cdp = await page.context().newCDPSession(page);
    let at: Point = { x: 0, y: 0 };
    const send = (type: "touchStart" | "touchMove" | "touchEnd", p?: Point) =>
      cdp.send("Input.dispatchTouchEvent", {
        type,
        touchPoints: p ? [{ x: Math.round(p.x), y: Math.round(p.y), id: 1 }] : [],
      });
    return {
      touch: true,
      down: async (p) => {
        at = p;
        await send("touchStart", p);
      },
      move: async (p, steps = 8) => {
        const from = at;
        for (let i = 1; i <= steps; i++) {
          at = {
            x: from.x + ((p.x - from.x) * i) / steps,
            y: from.y + ((p.y - from.y) * i) / steps,
          };
          await send("touchMove", at);
          await page.waitForTimeout(16);
        }
      },
      up: async () => {
        await send("touchEnd");
      },
    };
  }
  return {
    touch: false,
    down: async (p) => {
      await page.mouse.move(p.x, p.y);
      await page.mouse.down();
    },
    move: async (p, steps = 8) => {
      await page.mouse.move(p.x, p.y, { steps });
    },
    up: async () => {
      await page.mouse.up();
    },
  };
}

export async function centreOf(locator: Locator): Promise<Point> {
  const box = await locator.boundingBox();
  if (!box) throw new Error("not on screen");
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/** The tile's icon circle: where a finger presses an item. */
export function iconOf(tileLocator: Locator): Locator {
  return tileLocator.locator(".kt-tile__icon");
}

/** Press and hold an item until it lifts (FR-G01). */
export async function hold(page: Page, f: Finger, tileLocator: Locator): Promise<Point> {
  await tileLocator.scrollIntoViewIfNeeded();
  const at = await centreOf(iconOf(tileLocator));
  await f.down(at);
  await page.waitForTimeout(450);
  await expect(page.locator(".kt-place")).toHaveAttribute(
    "data-gesture",
    /lifted|dragging|collecting/,
  );
  return at;
}

export function bubble(page: Page, label: string | RegExp): Locator {
  return page
    .locator(".kt-bubble", { has: page.locator(".kt-bubble__label", { hasText: label }) })
    .locator(".kt-bubble__button");
}

export function bin(page: Page, place: "freezer" | "fridge" | "pantry"): Locator {
  return page.locator(`[data-kt-bin="${place}"]`);
}

export async function phase(page: Page): Promise<string> {
  return (await page.locator(".kt-place").getAttribute("data-gesture")) ?? "idle";
}
