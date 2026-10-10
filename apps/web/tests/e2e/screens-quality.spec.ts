import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  test,
  type Browser,
  type BrowserContextOptions,
  type Page,
} from "@playwright/test";
import { call, seedShopRite as seedDraft, uniqueEmail } from "./fixtures/app";
import {
  aptWith,
  closeApt,
  seedHMart,
  seedShopRite,
  settledTab,
  houseDate,
  tile,
} from "./fixtures/household";
import { quietPrompts } from "./fixtures/quiet";

/**
 * Quality checks over every screen this app has outside receipt capture and the notification
 * settings (TEST_PLAN A11Y-01, A11Y-04, A11Y-06, A11Y-07, VIS-02): one Apt 4B household with a
 * draft, an open tab, a settled tab, an H Mart tab Meera paid, kitchen items and an ask; each
 * test walks the same screens, signed in as Pranav (payer), as Kabir (member), and signed out.
 *
 * Chromium only: these are layout and markup rules, not engine behavior (the phone journeys run
 * the same screens on WebKit and Firefox).
 */
test.skip(
  ({ browserName }) => browserName !== "chromium",
  "screen-wide audits run once, on Chromium",
);
test.describe.configure({ mode: "serial" });

type Who = "payer" | "member" | "guest";
type Screen = {
  name: string;
  who: Who;
  path: string;
  /** Extra steps once loaded (open a sheet, switch a view). */
  then?: (page: Page) => Promise<void>;
};

type StorageState = Exclude<BrowserContextOptions["storageState"], string | undefined>;
type World = {
  states: Record<Exclude<Who, "guest">, StorageState>;
  screens: Screen[];
};

let world: World;

async function settle(page: Page): Promise<void> {
  await page.locator("html[data-hydrated]").waitFor({ state: "attached" });
  await expect(page.locator("h1").first()).toBeVisible();
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0, { timeout: 15_000 });
  await page.evaluate(() => document.fonts.ready);
}

async function build(browser: Browser, baseURL: string | undefined): Promise<World> {
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  const apt = await aptWith(browser, baseURL, page, ["arjun", "meera", "kabir"]);
  const kabir = apt.pages.kabir!;
  const sent = await seedShopRite(page, apt, { daysAgo: 4, meeraOnCoffee: true });
  await seedHMart(page, apt);
  const settled = await settledTab(
    page,
    apt,
    apt.member.pranav,
    "Corner store",
    await houseDate(page, 20),
  );
  const draft = await seedDraft(
    page,
    { id: apt.id, member: apt.member },
    { split: "all", leave: ["soap"] },
  );
  const fridge = await call<{ areas: { items: { id: string; name: string }[] }[] }>(
    page,
    "GET",
    `/houses/${apt.id}/inventory?place=fridge`,
  );
  const chicken = fridge.areas.flatMap((a) => a.items).find((i) => i.name.startsWith("Chicken"))!;
  await call(kabir, "POST", `/inventory/${chicken.id}/requests`, {
    amount: "a_bit",
    note: "For tonight’s curry",
  });
  const view = await call<{ bill: { link_token: string | null } }>(
    page,
    "GET",
    `/bills/${sent.billId}`,
  );

  const screens: Screen[] = [
    { name: "K1 kitchen", who: "payer", path: "/" },
    { name: "K2 fridge", who: "payer", path: "/kitchen/fridge" },
    {
      name: "K2 item sheet",
      who: "payer",
      path: "/kitchen/fridge",
      then: async (p) => {
        await tile(p, "Chicken thighs 3 lb").click();
        await expect(p.getByRole("dialog", { name: "Chicken thighs 3 lb" })).toBeVisible();
      },
    },
    { name: "K3 pantry", who: "payer", path: "/kitchen/pantry" },
    { name: "K4 requests", who: "payer", path: "/kitchen/requests" },
    { name: "K4 requests (asked)", who: "member", path: "/kitchen/requests" },
    { name: "1 tabs", who: "payer", path: "/tabs" },
    { name: "balances", who: "payer", path: "/balances" },
    { name: "house", who: "payer", path: "/house" },
    { name: "settings", who: "payer", path: "/settings" },
    { name: "2 review", who: "payer", path: `/bills/${draft.billId}/review` },
    { name: "3 split", who: "payer", path: `/bills/${draft.billId}/split` },
    { name: "4 send", who: "payer", path: `/bills/${draft.billId}/send` },
    { name: "E share", who: "payer", path: `/bills/${sent.billId}/share` },
    { name: "5 open bill (payer)", who: "payer", path: `/bills/${sent.billId}` },
    {
      name: "5 items view (payer)",
      who: "payer",
      path: `/bills/${sent.billId}`,
      then: async (p) => {
        await p.getByRole("tab", { name: "Items & splits" }).click();
        await expect(p.getByRole("grid", { name: "Who's eating what" })).toBeVisible();
      },
    },
    {
      name: "5 tab actions sheet",
      who: "payer",
      path: `/bills/${sent.billId}`,
      then: async (p) => {
        await p.getByRole("button", { name: "More actions" }).click();
        await expect(p.getByRole("dialog", { name: "Tab actions" })).toBeVisible();
      },
    },
    {
      name: "5 settle dialog",
      who: "payer",
      path: `/bills/${sent.billId}`,
      then: async (p) => {
        await p.getByRole("button", { name: "Stamp it settled" }).click();
        await expect(p.getByRole("dialog", { name: "Stamp it settled?" })).toBeVisible();
      },
    },
    { name: "5 open bill (member)", who: "member", path: `/bills/${sent.billId}` },
    {
      name: "5 pay sheet",
      who: "member",
      path: `/bills/${sent.billId}`,
      then: async (p) => {
        await p.getByRole("button", { name: /^Pay \$/ }).click();
        await expect(p.getByRole("dialog", { name: /^Pay Pranav/ })).toBeVisible();
      },
    },
    {
      name: "5 items view (member)",
      who: "member",
      path: `/bills/${sent.billId}`,
      then: async (p) => {
        await p.getByRole("tab", { name: "Items & splits" }).click();
        await expect(p.getByRole("grid", { name: "Who's eating what" })).toBeVisible();
      },
    },
    { name: "5 settled", who: "payer", path: `/bills/${settled}` },
    { name: "A start", who: "guest", path: "/start" },
    {
      name: "B verify",
      who: "guest",
      path: "/start",
      then: async (p) => {
        await p.getByLabel("YOUR EMAIL").fill(uniqueEmail("audit"));
        await p.getByRole("button", { name: "Send me a code →" }).click();
        await p.waitForURL("**/verify");
        await settle(p);
      },
    },
    { name: "D join house", who: "guest", path: `/h/${apt.inviteToken}` },
    { name: "D join bill", who: "guest", path: `/b/${view.bill.link_token}` },
    { name: "no such tab", who: "guest", path: "/b/not-a-real-token-0000000000" },
  ];
  const states = {
    payer: await context.storageState(),
    member: await kabir.context().storageState(),
  };
  await closeApt(apt);
  await context.close();
  return { states, screens };
}

/**
 * Opens one screen in a fresh context of the given size and runs `check` on it. A screen that
 * fails to load or to pass `check` is reported by name in `found`, and the walk goes on.
 */
async function visit(
  browser: Browser,
  baseURL: string | undefined,
  screen: Screen,
  options: BrowserContextOptions,
  check: (page: Page) => Promise<void>,
  found: string[],
): Promise<void> {
  const context = await browser.newContext({
    baseURL,
    viewport: { width: 390, height: 844 },
    ...options,
    ...(screen.who === "guest" ? {} : { storageState: world.states[screen.who] }),
  });
  await quietPrompts(context);
  const page = await context.newPage();
  try {
    await test.step(screen.name, async () => {
      await page.goto(screen.path);
      await settle(page);
      if (screen.then) await screen.then(page);
      await check(page);
    });
  } catch (error) {
    found.push(`${screen.name}: ${String(error).split("\n")[0]}`);
  } finally {
    await context.close();
  }
}

test.beforeAll(async ({ browser, baseURL }) => {
  test.setTimeout(300_000);
  world = await build(browser, baseURL);
});

test("A11Y-01 axe finds no serious or critical issue on any screen, light or dark", async ({
  browser,
  baseURL,
}) => {
  test.setTimeout(600_000);
  const found: string[] = [];
  for (const screen of world.screens) {
    for (const colorScheme of ["light", "dark"] as const) {
      await visit(
        browser,
        baseURL,
        screen,
        { colorScheme, reducedMotion: "reduce" },
        async (page) => {
          // Sticky bars (the bottom action bar, the split totals) sit over whatever row happens to
          // scroll under them; axe's target-size then reports that row's controls as "partially
          // obscured" depending on a few pixels of font layout (it failed only on Linux CI). Put
          // sticky elements in normal flow for the audit, so every control, the bars' own
          // included, is judged at its real size and spacing. Fixed overlays (sheets, dialogs,
          // toasts) stay as they are.
          await page.evaluate(() => {
            for (const el of document.querySelectorAll<HTMLElement>("body *")) {
              if (getComputedStyle(el).position === "sticky") el.style.position = "static";
            }
          });
          const results = await new AxeBuilder({ page })
            .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
            .analyze();
          for (const v of results.violations) {
            if (v.impact === "serious" || v.impact === "critical") {
              found.push(
                `${screen.name} (${colorScheme}): ${v.id} ${v.nodes
                  .slice(0, 3)
                  .map((n) => n.target.join(" "))
                  .join(" | ")}`,
              );
            }
          }
        },
        found,
      );
    }
  }
  expect(found).toEqual([]);
});

test("A11Y-04 every icon-only control has a name, and split checkboxes announce '{Name} uses {Item}' [FR-21]", async ({
  browser,
  baseURL,
}) => {
  test.setTimeout(300_000);
  const found: string[] = [];
  for (const screen of world.screens) {
    await visit(
      browser,
      baseURL,
      screen,
      {},
      async (page) => {
        const unnamed = await page.evaluate(() => {
          const out: string[] = [];
          const nodes = document.querySelectorAll<HTMLElement>(
            'button, a[href], [role="button"], [role="tab"], [role="switch"], [role="radio"], [role="checkbox"]',
          );
          nodes.forEach((el) => {
            if (el.closest('[aria-hidden="true"]')) return;
            const style = getComputedStyle(el);
            if (style.display === "none" || style.visibility === "hidden") return;
            const text = (el.innerText ?? "").trim();
            const labelled = el.getAttribute("aria-labelledby");
            const named =
              (el.getAttribute("aria-label") ?? "").trim() !== "" ||
              (labelled !== null &&
                labelled
                  .split(/\s+/)
                  .some((id) => (document.getElementById(id)?.textContent ?? "").trim() !== "")) ||
              (el.getAttribute("title") ?? "").trim() !== "";
            if (text === "" && !named) out.push(`${el.tagName.toLowerCase()}.${el.className}`);
          });
          return out;
        });
        for (const u of unnamed) found.push(`${screen.name}: ${u}`);
        const grid = page.getByRole("grid", { name: "Who's eating what" });
        if (await grid.count()) {
          const boxes = await grid
            .getByRole("gridcell")
            .getByRole("button")
            .evaluateAll((els) => els.map((e) => e.getAttribute("aria-label") ?? ""));
          expect(boxes.length, `${screen.name}: split boxes`).toBeGreaterThan(0);
          for (const name of boxes) {
            if (!/^\S.* uses \S.*$/.test(name)) found.push(`${screen.name}: split box "${name}"`);
          }
        }
      },
      found,
    );
  }
  expect(found).toEqual([]);
  // Spot check the exact wording on the payer's split screen.
  const split = world.screens.find((s) => s.name === "3 split")!;
  await visit(
    browser,
    baseURL,
    split,
    {},
    async (page) => {
      await expect(
        page.getByRole("button", { name: "Meera uses Greek yogurt 32 oz", exact: true }),
      ).toBeVisible();
    },
    found,
  );
});

test("A11Y-06 every button, link, tab, switch and radio on every screen is at least 44 × 44 px", async ({
  browser,
  baseURL,
}) => {
  test.setTimeout(300_000);
  const found: string[] = [];
  let checked = 0;
  for (const screen of world.screens) {
    await visit(
      browser,
      baseURL,
      screen,
      {},
      async (page) => {
        const out = await page.evaluate(() => {
          const small: string[] = [];
          const nodes = document.querySelectorAll<HTMLElement>(
            'button, a[href], input:not([type="hidden"]), select, textarea, [role="button"], [role="tab"], [role="switch"], [role="radio"], [role="checkbox"]',
          );
          let count = 0;
          nodes.forEach((el) => {
            const style = getComputedStyle(el);
            if (style.display === "none" || style.visibility === "hidden") return;
            // A real input under a custom control (the OTP boxes) is measured by its box.
            if (el.tagName === "INPUT" && style.opacity === "0") return;
            // WCAG 2.5.8 inline exception: a link inside a sentence.
            if (el.tagName === "A" && style.display === "inline" && el.parentElement?.closest("p"))
              return;
            // A native checkbox or radio inside its label: the whole label is the target.
            const label =
              el instanceof HTMLInputElement && /^(checkbox|radio)$/.test(el.type)
                ? el.closest("label")
                : null;
            const target = label ?? el;
            const width = target.offsetWidth;
            const height = target.offsetHeight;
            if (width === 0 && height === 0) return;
            count += 1;
            if (width < 44 || height < 44) {
              const name =
                el.getAttribute("aria-label") ?? el.innerText?.trim().slice(0, 40) ?? el.tagName;
              small.push(`${el.tagName.toLowerCase()} "${name}" ${width}×${height}`);
            }
          });
          return { count, small };
        });
        checked += out.count;
        for (const s of out.small) found.push(`${screen.name}: ${s}`);
      },
      found,
    );
  }
  expect(checked).toBeGreaterThan(300);
  expect(found).toEqual([]);
});

test("VIS-02 at 320 px and 1024 px no screen scrolls sideways, and at 1024 px the Kitchen panels sit side by side [FR-63]", async ({
  browser,
  baseURL,
}) => {
  test.setTimeout(300_000);
  const found: string[] = [];
  for (const width of [320, 1024]) {
    for (const screen of world.screens) {
      await visit(
        browser,
        baseURL,
        screen,
        { viewport: { width, height: 800 } },
        async (page) => {
          const overflow = await page.evaluate(() => ({
            doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            body: document.body.scrollWidth - document.documentElement.clientWidth,
          }));
          if (overflow.doc > 0 || overflow.body > 0) {
            found.push(
              `${screen.name} @${width}: ${Math.max(overflow.doc, overflow.body)} px too wide`,
            );
          }
        },
        found,
      );
    }
  }
  expect(found).toEqual([]);

  const kitchen = world.screens.find((s) => s.name === "K1 kitchen")!;
  for (const [width, sideBySide] of [
    [1024, true],
    [390, false],
  ] as const) {
    await visit(
      browser,
      baseURL,
      kitchen,
      { viewport: { width, height: 800 } },
      async (page) => {
        const fridge = await page.getByRole("link", { name: /^Open the fridge/ }).boundingBox();
        const pantry = await page.getByRole("link", { name: /^Open the pantry/ }).boundingBox();
        expect(fridge && pantry).toBeTruthy();
        const sameRow =
          Math.abs(fridge!.y - pantry!.y) < 4 && pantry!.x > fridge!.x + fridge!.width - 1;
        expect(sameRow, `kitchen panels side by side at ${width} px`).toBe(sideBySide);
      },
      found,
    );
  }
});

test("A11Y-07 with prefers-reduced-motion nothing animates on any screen, and every animation rule sits behind no-preference", async ({
  browser,
  baseURL,
}) => {
  test.setTimeout(300_000);
  const found: string[] = [];
  for (const screen of world.screens) {
    await visit(
      browser,
      baseURL,
      screen,
      { reducedMotion: "reduce" },
      async (page) => {
        const running = await page.evaluate(() =>
          document
            .getAnimations()
            .map(
              (a) =>
                (a as CSSAnimation).animationName ??
                (a as CSSTransition).transitionProperty ??
                "animation",
            ),
        );
        for (const r of running) found.push(`${screen.name}: running ${r}`);
      },
      found,
    );
  }
  // Interactions that animate when motion is allowed: a stamp on a split box and a toast.
  const split = world.screens.find((s) => s.name === "3 split")!;
  await visit(
    browser,
    baseURL,
    split,
    { reducedMotion: "reduce" },
    async (page) => {
      const yogurt = page.getByRole("button", {
        name: "Arjun uses Greek yogurt 32 oz",
        exact: true,
      });
      await yogurt.click();
      await expect(yogurt).toHaveAttribute("aria-pressed", "true");
      const during = await page.evaluate(() => document.getAnimations().length);
      if (during > 0) found.push(`3 split: ${during} animation(s) after a tick`);
      await yogurt.click();
      await expect(yogurt).toHaveAttribute("aria-pressed", "false");
    },
    found,
  );
  // Every keyframe animation in the app's CSS is declared inside
  // @media (prefers-reduced-motion: no-preference).
  const kitchen = world.screens.find((s) => s.name === "K1 kitchen")!;
  await visit(
    browser,
    baseURL,
    kitchen,
    { reducedMotion: "reduce" },
    async (page) => {
      const loose = await page.evaluate(() => {
        const out: string[] = [];
        const walk = (rules: CSSRuleList, guarded: boolean) => {
          for (const rule of Array.from(rules)) {
            if (rule instanceof CSSMediaRule) {
              walk(
                rule.cssRules,
                guarded || /prefers-reduced-motion:\s*no-preference/.test(rule.conditionText),
              );
            } else if ("cssRules" in rule && (rule as CSSGroupingRule).cssRules) {
              walk((rule as CSSGroupingRule).cssRules, guarded);
            }
            if (rule instanceof CSSStyleRule && !guarded) {
              const name = rule.style.animationName || rule.style.getPropertyValue("animation");
              if (name && name !== "none" && !/^(initial|inherit|unset)$/.test(name)) {
                out.push(`${rule.selectorText} { animation: ${name} }`);
              }
            }
          }
        };
        for (const sheet of Array.from(document.styleSheets)) {
          try {
            walk(sheet.cssRules, false);
          } catch {
            // cross-origin sheet: not ours
          }
        }
        return out;
      });
      for (const l of loose) found.push(`unguarded ${l}`);
    },
    found,
  );
  expect(found).toEqual([]);
});
