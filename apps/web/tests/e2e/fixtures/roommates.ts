import {
  test as base,
  devices,
  expect,
  type Browser,
  type BrowserContext,
  type BrowserContextOptions,
  type Page,
} from "@playwright/test";

/**
 * Multi-roommate harness (TEST_PLAN §2): `roommates(n)` opens n isolated browser contexts on
 * alternating phone profiles and `expectEverywhere(fn, {within})` asserts that every one of them
 * converges on the same state inside the window.
 */

export type Roommate = {
  index: number;
  /** Device profile name, e.g. "iPhone 14". */
  device: string;
  context: BrowserContext;
  page: Page;
};

export type SignIn = (page: Page, roommate: { index: number; device: string }) => Promise<void>;

export type RoommatesOptions = {
  /** Signs each roommate in (Milestone 3 plugs the fake OTP/OIDC flow in here). */
  signIn?: SignIn;
};

export type ExpectEverywhereOptions = {
  /** Convergence window in ms (default 2000, the realtime target in PRD §11). */
  within?: number;
  /** Poll intervals in ms. */
  intervals?: number[];
};

export type House = {
  mates: Roommate[];
  pages: Page[];
  expectEverywhere: (
    fn: (page: Page, mate: Roommate) => Promise<void>,
    options?: ExpectEverywhereOptions,
  ) => Promise<void>;
};

const PROFILES = ["iPhone 14", "Pixel 7"] as const;

/**
 * The device descriptor minus what the current browser cannot emulate. Firefox has no mobile
 * emulation (`isMobile`), so it keeps the phone viewport, user agent, scale and touch only.
 */
export function profileFor(
  browser: Browser,
  index: number,
): { device: string; options: BrowserContextOptions } {
  const device = PROFILES[index % PROFILES.length]!;
  const options: BrowserContextOptions & { defaultBrowserType?: string } = { ...devices[device] };
  delete options.defaultBrowserType;
  if (browser.browserType().name() === "firefox") delete options.isMobile;
  return { device, options };
}

/** Asserts `fn` passes on every page within the window, polling each page independently. */
export async function expectEverywhere(
  mates: Roommate[],
  fn: (page: Page, mate: Roommate) => Promise<void>,
  { within = 2000, intervals = [50, 100, 200] }: ExpectEverywhereOptions = {},
): Promise<void> {
  await Promise.all(
    mates.map((mate) =>
      expect(async () => fn(mate.page, mate), `roommate ${mate.index} (${mate.device})`).toPass({
        timeout: within,
        intervals,
      }),
    ),
  );
}

type Fixtures = {
  roommates: (n: number, options?: RoommatesOptions) => Promise<House>;
};

export const test = base.extend<Fixtures>({
  roommates: async ({ browser, baseURL }, provide) => {
    const opened: BrowserContext[] = [];
    await provide(async (n, options = {}) => {
      if (!Number.isInteger(n) || n < 1) throw new Error(`roommates(n) needs n >= 1, got ${n}`);
      const mates: Roommate[] = [];
      for (let index = 0; index < n; index++) {
        const { device, options: contextOptions } = profileFor(browser, index);
        const context = await browser.newContext({ ...contextOptions, baseURL });
        opened.push(context);
        const page = await context.newPage();
        if (options.signIn) await options.signIn(page, { index, device });
        mates.push({ index, device, context, page });
      }
      return {
        mates,
        pages: mates.map((m) => m.page),
        expectEverywhere: (fn, opts) => expectEverywhere(mates, fn, opts),
      };
    });
    await Promise.all(opened.map((context) => context.close()));
  },
});

export { expect };
