import type { BrowserContext } from "@playwright/test";

/**
 * This device already saw the install card twice and said "Not now" twice (PRD §20.1–20.2), so
 * no install or notification prompt covers the screen in tests that are about something else.
 * Same keys as lib/pwa/local.ts.
 */
export async function quietPrompts(context: BrowserContext): Promise<void> {
  await context.addInitScript(() => {
    try {
      localStorage.setItem("kt.pwa.installShown", "2");
      localStorage.setItem("kt.pwa.n1NotNow", "2");
    } catch {
      // storage blocked: nothing to quiet
    }
  });
}
