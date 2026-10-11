import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { call } from "./fixtures/app";
import {
  addLeftovers,
  aptWith,
  closeApt,
  open,
  seedShopRite,
  tile,
  type Apt,
} from "./fixtures/household";
import {
  bin,
  bubble,
  centreOf,
  finger,
  hold,
  iconOf,
  phase,
  type Finger,
} from "./fixtures/gestures";

/**
 * Kitchen gestures against the real API (docs/features/kitchen-gestures/SPEC.md; TEST_PLAN 3.13).
 * Phones: WebKit iPhone and Chromium Pixel. Chromium presses with real touch (CDP), WebKit with
 * real pointer events from the mouse. Firefox desktop runs the keyboard path only (A11Y-G1).
 */

const PHONES = ({ browserName }: { browserName: string }) => browserName === "firefox";

type Inv = { areas: { area: string; items: { id: string; name: string; place: string }[] }[] };

async function places(page: Page, houseId: string): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  for (const place of ["fridge", "pantry"]) {
    const view = await call<Inv>(page, "GET", `/houses/${houseId}/inventory?place=${place}`);
    for (const item of view.areas.flatMap((a) => a.items)) out[item.name] = item.place;
  }
  return out;
}

function refrigerated(page: Page) {
  return page.locator('[data-kt-area="fridge"] .kt-tile');
}

async function names(page: Page): Promise<string[]> {
  return refrigerated(page).evaluateAll((tiles) =>
    tiles.map((t) => (t.getAttribute("aria-label") ?? "").split(",")[0]!),
  );
}

/** Lift, then leave the fan's cone (straight down: the fan sits above or below) to drag. */
async function startDrag(page: Page, f: Finger, name: string) {
  const at = await hold(page, f, tile(page, name));
  const fanBelow = await page.evaluate(() => {
    const b = document.querySelector<HTMLElement>(".kt-bubble");
    return b ? /, ([-\d.]+)px/.exec(b.style.transform)?.[1] : null;
  });
  const down = fanBelow !== null && Number(fanBelow) > at.y ? -1 : 1;
  await f.move({ x: at.x + 2, y: at.y + 24 * down }, 3);
  await expect.poll(() => phase(page)).toBe("dragging");
  return at;
}

async function kitchenApt(page: Page, browser: Parameters<typeof aptWith>[0], baseURL?: string) {
  const apt = await aptWith(browser, baseURL, page, ["meera"]);
  await seedShopRite(page, apt);
  return apt;
}

test.describe("gestures on a phone", () => {
  test.skip(PHONES, "touch journeys: WebKit and Chromium phones");

  test("GEST-06 reorder eggs to slot 2; after a reload the order is still there, for that member only [FR-G02]", async ({
    page,
    browser,
    baseURL,
    browserName,
  }) => {
    test.setTimeout(150_000);
    const apt = await kitchenApt(page, browser, baseURL);
    const meera = apt.pages.meera!;
    await open(page, "/kitchen/fridge");
    await expect(refrigerated(page)).toHaveCount(6);
    const before = await names(page);
    expect(before.indexOf("Eggs 18 ct")).toBeGreaterThan(1);
    const f = await finger(page, browserName);
    await startDrag(page, f, "Eggs 18 ct");
    const slot2 = (await refrigerated(page).nth(1).boundingBox())!;
    // The leading third of the second cell: the gap moves before that item.
    await f.move({ x: slot2.x + slot2.width * 0.15, y: slot2.y + 20 }, 10);
    await f.up();
    await expect
      .poll(() => names(page))
      .toEqual([before[0], "Eggs 18 ct", ...before.slice(1).filter((n) => n !== "Eggs 18 ct")]);
    const mine = await names(page);
    await page.reload();
    await expect(refrigerated(page)).toHaveCount(before.length);
    expect(await names(page)).toEqual(mine);
    await open(meera, "/kitchen/fridge");
    await expect(refrigerated(meera)).toHaveCount(before.length);
    expect(await names(meera)).toEqual(before);
    await closeApt(apt);
  });

  test("GEST-07 hold, slide onto Pantry, release: it moves, a toast offers Undo, and Undo puts it back [FR-G03] [FR-G04] [FR-G08]", async ({
    page,
    browser,
    baseURL,
    browserName,
  }) => {
    test.setTimeout(120_000);
    const apt = await kitchenApt(page, browser, baseURL);
    await open(page, "/kitchen/fridge");
    const f = await finger(page, browserName);
    await hold(page, f, tile(page, "Whole milk 1 gal"));
    await expect(bubble(page, "Pantry")).toBeVisible();
    await expect(bubble(page, "Freezer")).toBeVisible();
    await f.move(await centreOf(bubble(page, "Pantry")), 8);
    await expect(page.getByText("MOVE TO PANTRY")).toBeVisible();
    await f.up();
    await expect(page.getByRole("status").getByText(/→ Pantry/)).toBeVisible();
    await expect(tile(page, "Whole milk 1 gal")).toHaveCount(0);
    expect((await places(page, apt.id))["Whole milk 1 gal"]).toBe("pantry");
    await page.getByRole("button", { name: "Undo" }).click();
    await expect(tile(page, "Whole milk 1 gal")).toBeVisible();
    expect((await places(page, apt.id))["Whole milk 1 gal"]).toBe("fridge");
    await closeApt(apt);
  });

  test("GEST-08 hold, slide onto Ask Meera: a small ask reaches Meera, and Undo cancels it [FR-G04] [FR-G08]", async ({
    page,
    browser,
    baseURL,
    browserName,
  }) => {
    test.setTimeout(120_000);
    const apt = await kitchenApt(page, browser, baseURL);
    const meera = apt.pages.meera!;
    await open(page, "/kitchen/fridge");
    const f = await finger(page, browserName);
    await hold(page, f, tile(page, "Greek yogurt 32 oz"));
    await f.move(await centreOf(bubble(page, "Ask Meera")), 8);
    await expect(page.getByText("ASK MEERA FOR SOME")).toBeVisible();
    await f.up();
    await expect(page.getByText(/Asked Meera for some/)).toBeVisible();
    const inbox = await call<{ requests: { status: string; amount: string }[] }>(
      meera,
      "GET",
      `/houses/${apt.id}/requests?box=in`,
    );
    expect(inbox.requests.map((r) => [r.status, r.amount])).toEqual([["pending", "a_bit"]]);
    await page.getByRole("button", { name: "Undo" }).click();
    await expect
      .poll(async () =>
        (
          await call<{ requests: { status: string }[] }>(
            meera,
            "GET",
            `/houses/${apt.id}/requests?box=in`,
          )
        ).requests.map((r) => r.status),
      )
      .toEqual(["cancelled"]);
    await closeApt(apt);
  });

  test("GEST-09 my own item offers Used up; an item open to share offers Help yourself and asks nothing [FR-G04]", async ({
    page,
    browser,
    baseURL,
    browserName,
  }) => {
    test.setTimeout(120_000);
    const apt = await kitchenApt(page, browser, baseURL);
    const meera = apt.pages.meera!;
    const ids = await call<Inv>(meera, "GET", `/houses/${apt.id}/inventory?place=fridge`);
    const yogurt = ids.areas.flatMap((a) => a.items).find((i) => i.name.startsWith("Greek"))!;
    await call(meera, "PUT", `/inventory/${yogurt.id}/sharing`, { open: true });
    await open(page, "/kitchen/fridge");
    const f = await finger(page, browserName);
    await hold(page, f, tile(page, "Eggs 18 ct"));
    await expect(bubble(page, "Used up")).toBeVisible();
    await f.move(await centreOf(bubble(page, "Used up")), 8);
    await f.up();
    await expect(page.getByText(/marked used up/)).toBeVisible();
    await expect(tile(page, "Eggs 18 ct")).toHaveCount(0);

    await hold(page, f, tile(page, "Greek yogurt 32 oz"));
    await expect(bubble(page, "Help yourself")).toBeVisible();
    await f.move(await centreOf(bubble(page, "Help yourself")), 8);
    await f.up();
    await expect(page.getByText("Open to share — help yourself")).toBeVisible();
    const out = await call<{ requests: unknown[] }>(
      page,
      "GET",
      `/houses/${apt.id}/requests?box=out`,
    );
    expect(out.requests).toEqual([]);
    await closeApt(apt);
  });

  test("GEST-10 select three and move them to the Pantry in one request; mixed picks skip what's already there [FR-G05] [FR-G08]", async ({
    page,
    browser,
    baseURL,
  }) => {
    test.setTimeout(120_000);
    const apt = await kitchenApt(page, browser, baseURL);
    await addLeftovers(page, apt, { name: "Frozen peas", place: "freezer" });
    await open(page, "/kitchen/fridge");
    const moves: number[] = [];
    page.on("request", (r) => {
      if (r.url().endsWith("/items/move"))
        moves.push((r.postDataJSON() as { moves: unknown[] }).moves.length);
    });
    await page.getByRole("button", { name: "Select items to move" }).click();
    await expect(page.getByText("Tap items to pick them.")).toBeVisible();
    for (const name of ["Whole milk 1 gal", "Chicken thighs 3 lb", "Baby spinach"])
      await tile(page, name).click();
    const bar = page.getByRole("toolbar", { name: /3 selected/ });
    await expect(bar).toBeVisible();
    await expect(bar.getByRole("button", { name: "Fridge" })).toHaveCount(0); // all already there
    await bar.getByRole("button", { name: "Pantry" }).click();
    await expect(page.getByText("3 items → Pantry")).toBeVisible();
    expect(moves).toEqual([3]);
    await expect(page.getByRole("button", { name: "Select items to move" })).toBeVisible(); // select mode ended

    await page.getByRole("button", { name: "Select items to move" }).click();
    await tile(page, "Eggs 18 ct").click();
    await tile(page, "Frozen peas").click();
    await page
      .getByRole("toolbar", { name: /2 selected/ })
      .getByRole("button", { name: "Freezer" })
      .click();
    await expect(page.getByText("1 moved · 1 already there")).toBeVisible();
    expect(moves).toEqual([3, 2]);
    const where = await places(page, apt.id);
    expect([
      where["Whole milk 1 gal"],
      where["Chicken thighs 3 lb"],
      where["Baby spinach"],
    ]).toEqual(["pantry", "pantry", "pantry"]);
    expect(where["Eggs 18 ct"]).toBe("freezer");
    await closeApt(apt);
  });

  test("GEST-11 drag to collect three onto the Pantry bin; HERE refuses; a drop in the list sends them home [FR-G06] [FR-G07]", async ({
    page,
    browser,
    baseURL,
    browserName,
  }) => {
    test.setTimeout(150_000);
    const apt = await kitchenApt(page, browser, baseURL);
    await open(page, "/kitchen/fridge");
    const f = await finger(page, browserName);

    // A drop in the list: the collected items fly home, nothing moves.
    await startDrag(page, f, "Whole milk 1 gal");
    await f.move(await centreOf(iconOf(tile(page, "Oat milk"))), 8);
    await page.waitForTimeout(400);
    await expect.poll(() => phase(page)).toBe("collecting");
    await expect(page.locator(".kt-lifted__count")).toHaveText("2");
    await f.move(await centreOf(iconOf(tile(page, "Baby spinach"))), 8);
    await f.up();
    await expect.poll(() => phase(page)).toBe("idle");
    expect((await places(page, apt.id))["Oat milk"]).toBe("fridge");

    // HERE: every dragged item is already in the fridge.
    await startDrag(page, f, "Eggs 18 ct");
    await expect(bin(page, "fridge")).toHaveText(/HERE/);
    await f.move(await centreOf(bin(page, "fridge")), 10);
    await f.up();
    await expect(page.getByText("Already in the fridge")).toBeVisible();
    expect((await places(page, apt.id))["Eggs 18 ct"]).toBe("fridge");

    // Collect three, drop them on the Pantry bin.
    await startDrag(page, f, "Whole milk 1 gal");
    for (const name of ["Chicken thighs 3 lb", "Baby spinach"]) {
      await f.move(await centreOf(iconOf(tile(page, name))), 8);
      await page.waitForTimeout(400);
    }
    await expect(page.locator(".kt-lifted__count")).toHaveText("3");
    await expect(page.locator(".kt-tile[data-ghost]")).toHaveCount(3);
    await f.move(await centreOf(bin(page, "pantry")), 10);
    await expect(bin(page, "pantry")).toHaveAttribute("data-over");
    await f.up();
    await expect(page.getByText("3 items → Pantry")).toBeVisible();
    const where = await places(page, apt.id);
    expect([
      where["Whole milk 1 gal"],
      where["Chicken thighs 3 lb"],
      where["Baby spinach"],
    ]).toEqual(["pantry", "pantry", "pantry"]);
    await closeApt(apt);
  });

  test("GEST-12 the page holds still under a drag but scrolls when the finger moves before the lift [FR-G01] [FR-G11]", async ({
    page,
    browser,
    baseURL,
    browserName,
  }) => {
    test.skip(browserName !== "chromium", "native touch scrolling needs real touch (CDP)");
    test.setTimeout(120_000);
    const apt = await kitchenApt(page, browser, baseURL);
    for (let i = 0; i < 12; i++)
      await addLeftovers(page, apt, { name: `Soup ${i + 1}`, place: "fridge" });
    await page.setViewportSize({ width: 412, height: 560 });
    await open(page, "/kitchen/fridge");
    const f = await finger(page, browserName);
    const start = await centreOf(iconOf(tile(page, "Whole milk 1 gal")));
    await f.down(start);
    await f.move({ x: start.x, y: start.y - 160 }, 6);
    await f.up();
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(40);
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);

    await startDrag(page, f, "Whole milk 1 gal");
    const at = await centreOf(page.locator(".kt-lifted"));
    await f.move({ x: at.x + 60, y: at.y - 100 }, 6);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    await f.up();
    await closeApt(apt);
  });

  test("GEST-13 a long-press selects no text, shows no callout and no context menu [FR-G01]", async ({
    page,
    browser,
    baseURL,
    browserName,
  }) => {
    test.setTimeout(120_000);
    const apt = await kitchenApt(page, browser, baseURL);
    await open(page, "/kitchen/fridge");
    const milk = tile(page, "Whole milk 1 gal");
    const style = await milk.evaluate((el) => {
      const s = getComputedStyle(el) as CSSStyleDeclaration & { webkitTouchCallout?: string };
      return {
        select: s.userSelect || s.webkitUserSelect,
        callout: s.webkitTouchCallout ?? "none",
      };
    });
    expect(style).toEqual({ select: "none", callout: "none" });
    expect(await milk.evaluate((el) => el.tagName)).toBe("BUTTON"); // no link preview on iOS
    const f = await finger(page, browserName);
    await hold(page, f, milk);
    expect(await page.evaluate(() => window.getSelection()?.toString() ?? "")).toBe("");
    // A long-press's context menu (touch) is swallowed; it doesn't open anything.
    const prevented = await milk.evaluate((el) => {
      const ev = new PointerEvent("contextmenu", {
        bubbles: true,
        cancelable: true,
        pointerType: "touch",
      });
      el.dispatchEvent(ev);
      return ev.defaultPrevented;
    });
    expect(prevented).toBe(true);
    await expect(page.locator('.kt-fan[role="menu"]')).toHaveCount(0);
    await f.up();
    // Releasing a hold without moving leaves the bubbles as ordinary buttons (MENU_STATIC).
    await expect(page.locator('.kt-fan[role="menu"]')).toHaveCount(1);
    await page.keyboard.press("Escape");
    await expect(page.locator(".kt-fan")).toHaveCount(0);
    await closeApt(apt);
  });
});

/** A draft with the eight sort-before items, all mine, ready for Review & send. */
async function stockingDraft(page: Page, apt: Apt): Promise<string> {
  const bill = await call<{ id: string }>(page, "POST", `/houses/${apt.id}/bills`, {
    store_name: "ShopRite",
    purchase_date: "2026-10-09",
  });
  const lines: Array<[string, number, string, string]> = [
    ["Frozen dumplings", 899, "freezer", "dumplings"],
    ["Ice cream", 649, "freezer", "ice_cream"],
    ["Whole milk", 429, "fridge", "milk"],
    ["Greek yogurt", 679, "fridge", "yogurt"],
    ["Eggs, 18 ct", 549, "fridge", "eggs"],
    ["Chicken thighs", 1137, "fridge", "chicken"],
    ["Jasmine rice", 999, "pantry", "rice"],
    ["Ground coffee", 849, "pantry", "coffee"],
  ];
  const changes = [];
  for (const [name, cents, storage, icon_key] of lines) {
    const item = await call<{ id: string }>(page, "POST", `/bills/${bill.id}/items`, {
      name,
      line_total_cents: cents,
      storage,
      icon_key,
    });
    changes.push({ item_id: item.id, participant: `m:${apt.member.pranav}`, on: true });
  }
  await call(page, "PUT", `/bills/${bill.id}/allocations`, { changes });
  return bill.id;
}

test.describe("stocking animation on Send", () => {
  test.skip(PHONES, "phones");

  test("ANIM-02 Share opens only when the send and the animation are both done; a failed send flies the food home and stocks nothing [FR-G10]", async ({
    page,
    browser,
    baseURL,
  }) => {
    test.setTimeout(120_000);
    const apt = await aptWith(browser, baseURL, page);
    const billId = await stockingDraft(page, apt);

    // Failure first: the server refuses, the icons go home, the bins stay empty.
    await page.route(`**/api/v1/bills/${billId}/send`, (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/problem+json",
        body: JSON.stringify({ title: "Try again" }),
      }),
    );
    await open(page, `/bills/${billId}/send`);
    await expect(page.locator("[data-stock-row]")).toHaveCount(8);
    await page.getByRole("button", { name: /Send it/ }).click();
    await expect(page.getByText("Try again")).toBeVisible();
    await expect(page.locator(".kt-stock-bin__count")).toHaveCount(0);
    await expect(page.locator(".kt-stocking[data-dim]")).toHaveCount(0);
    expect(page.url()).toContain("/send");
    const fridge = await call<Inv>(page, "GET", `/houses/${apt.id}/inventory?place=fridge`);
    expect(fridge.areas.flatMap((a) => a.items)).toEqual([]);
    await page.unroute(`**/api/v1/bills/${billId}/send`);

    // A slow server: the food lands first, then Review & send waits for the answer.
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    await page.route(`**/api/v1/bills/${billId}/send`, async (route) => {
      await held;
      await route.continue();
    });
    await page.getByRole("button", { name: /Send it/ }).click();
    await expect(page.locator(".kt-stock-bin__count")).toHaveCount(3);
    await expect(page.locator('[data-stock-bin="fridge"] .kt-stock-bin__count')).toHaveText("+4");
    await expect(page.locator('[data-stock-bin="freezer"] .kt-stock-bin__count')).toHaveText("+2");
    await page.waitForTimeout(300);
    expect(page.url()).toContain("/send");
    release();
    await page.waitForURL(`**/bills/${billId}/share`);
    await page.unroute(`**/api/v1/bills/${billId}/send`);

    // A fast server still waits for the food to land (~420 ms for eight).
    const second = await stockingDraft(page, apt);
    await open(page, `/bills/${second}/send`);
    const started = Date.now();
    await page.getByRole("button", { name: /Send it/ }).click();
    await page.waitForURL(`**/bills/${second}/share`);
    expect(Date.now() - started).toBeGreaterThanOrEqual(420);
    await closeApt(apt);
  });

  test("ANIM-03 reduced motion: nothing flies, the rows dim and the counts fade in [FR-G10]", async ({
    browser,
    baseURL,
  }) => {
    test.setTimeout(120_000);
    const context = await browser.newContext({
      baseURL,
      reducedMotion: "reduce",
      viewport: { width: 390, height: 844 },
    });
    const page = await context.newPage();
    const apt = await aptWith(browser, baseURL, page);
    const billId = await stockingDraft(page, apt);
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    await page.route(`**/api/v1/bills/${billId}/send`, async (route) => {
      await held;
      await route.continue();
    });
    await open(page, `/bills/${billId}/send`);
    await page.getByRole("button", { name: /Send it/ }).click();
    await expect(page.locator(".kt-stocking[data-dim]")).toBeVisible();
    await expect(page.locator(".kt-stock-bin__count")).toHaveCount(3);
    const moved = await page
      .locator("[data-fly-x], [data-fly-y]")
      .evaluateAll((els) => els.filter((e) => getComputedStyle(e).transform !== "none").length);
    expect(moved).toBe(0);
    release();
    await page.waitForURL(`**/bills/${billId}/share`);
    await closeApt(apt);
    await context.close();
  });
});

test("A11Y-G1 select mode, the quick-action menu and the drop bar are axe-clean in both themes; the keyboard can move, ask and reorder [FR-G05] [FR-G11]", async ({
  page,
  browser,
  baseURL,
  browserName,
}) => {
  test.setTimeout(180_000);
  const apt = await kitchenApt(page, browser, baseURL);
  await open(page, "/kitchen/fridge");
  const blocking: string[] = [];
  async function axe(state: string) {
    for (const colorScheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme });
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
        .analyze();
      for (const v of results.violations) {
        if (v.impact === "serious" || v.impact === "critical")
          blocking.push(`${state} ${colorScheme}: ${v.id}`);
      }
    }
  }

  // Keyboard: Shift+F10 on a focused item opens the menu; arrows move; Enter chooses.
  await tile(page, "Whole milk 1 gal").focus();
  await page.keyboard.press("Shift+F10");
  const menu = page.getByRole("menu", { name: /Quick actions for/ });
  await expect(menu).toBeVisible();
  await expect(menu.getByRole("menuitem").first()).toBeFocused();
  await axe("menu");
  await page.keyboard.press("ArrowRight");
  await expect(menu.getByRole("menuitem", { name: /Pantry/ })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByText(/→ Pantry/)).toBeVisible();
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(tile(page, "Whole milk 1 gal")).toBeVisible();

  // Select mode: Space toggles the focused item; the bar announces the count.
  await page.getByRole("button", { name: "Select items to move" }).click();
  await tile(page, "Eggs 18 ct").focus();
  await page.keyboard.press("Space");
  await expect(page.getByRole("toolbar", { name: /1 selected/ })).toBeVisible();
  await axe("select mode");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Select items to move" })).toBeVisible();

  // The drop bar, mid-drag (pointer only).
  if (browserName !== "firefox") {
    const f = await finger(page, browserName);
    await startDrag(page, f, "Baby spinach");
    await expect(page.getByRole("group", { name: "Drop to move" })).toBeVisible();
    await axe("drop bar");
    await page.keyboard.press("Escape");
    await f.up();
  }

  // The item sheet: Move to, Move earlier / later, and Ask, all from the keyboard.
  await tile(page, "Chicken thighs 3 lb").focus();
  await page.keyboard.press("Enter");
  const sheet = page.getByRole("dialog", { name: /Chicken thighs/ });
  await expect(sheet.getByRole("button", { name: "Move earlier" })).toBeVisible();
  const before = await names(page);
  await sheet.getByRole("button", { name: "Move earlier" }).focus();
  await page.keyboard.press("Enter");
  await expect.poll(() => names(page)).not.toEqual(before);
  await sheet.getByRole("button", { name: "Pantry" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText(/→ Pantry/)).toBeVisible();
  await tile(page, "Greek yogurt 32 oz").focus();
  await page.keyboard.press("Enter");
  const ask = page.getByRole("dialog", { name: /Greek yogurt/ });
  await ask.getByRole("button", { name: /^Ask/ }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Request sent to Meera")).toBeVisible();

  expect(blocking).toEqual([]);
  await closeApt(apt);
});
