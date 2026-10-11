import { describe, expect, it, vi } from "vitest";

// next/font only works inside the Next compiler; the layout's viewport export is what we test.
vi.mock("next/font/google", () => ({
  Space_Mono: () => ({ variable: "font-space-mono" }),
  Work_Sans: () => ({ variable: "font-work-sans" }),
}));

const { viewport } = await import("@/app/layout");
const { default: manifest } = await import("@/app/manifest");
const { THEME_COLORS, setPreference } = await import("@/lib/theme");

describe("theme color and manifest", () => {
  it("PWA-10 theme-color meta follows light and dark color schemes", () => {
    expect(viewport.themeColor).toEqual([
      { media: "(prefers-color-scheme: light)", color: "#ECEAE2" },
      { media: "(prefers-color-scheme: dark)", color: "#0D0D0B" },
    ]);
    expect(viewport.colorScheme).toBe("light dark");
  });

  it("PWA-10 an explicit theme choice repoints theme-color, and system restores it", () => {
    document.head.innerHTML =
      '<meta name="theme-color" media="(prefers-color-scheme: light)" content="#ECEAE2">' +
      '<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#0D0D0B">';
    const contents = () =>
      Array.from(document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')).map(
        (m) => m.content,
      );
    setPreference("dark");
    expect(contents()).toEqual([THEME_COLORS.dark, THEME_COLORS.dark]);
    setPreference("light");
    expect(contents()).toEqual([THEME_COLORS.light, THEME_COLORS.light]);
    setPreference("system");
    expect(contents()).toEqual(["#ECEAE2", "#0D0D0B"]);
    document.head.innerHTML = "";
  });

  it("manifest matches the PRD §20.1 table", () => {
    const m = manifest();
    expect(m).toMatchObject({
      id: "/",
      start_url: "/?source=pwa",
      scope: "/",
      name: "KitchenTab",
      short_name: "KitchenTab",
      display: "standalone",
      display_override: ["standalone"],
      orientation: "portrait",
      background_color: "#ECEAE2",
      theme_color: "#121210",
    });
    expect(m.icons).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ sizes: "192x192", purpose: "any" }),
        expect.objectContaining({ sizes: "512x512", purpose: "any" }),
        expect.objectContaining({ sizes: "512x512", purpose: "maskable" }),
      ]),
    );
    expect(m.shortcuts?.map((s) => [s.name, s.url])).toEqual([
      ["Snap a receipt", "/bills/new"],
      ["Fridge", "/kitchen/fridge"],
      ["Asks", "/kitchen/requests"],
    ]);
  });
});

describe("busy screens (PUSH-01)", () => {
  it("PUSH-01 the send and share screens count as busy by URL, before they render [FR-42]", async () => {
    const { MID_SEND, busyNow } = await import("../../lib/pwa/register");
    for (const path of ["/bills/b1/send", "/bills/b1/share", "/bills/b1/share/"])
      expect(MID_SEND.test(path), path).toBe(true);
    for (const path of ["/bills/b1", "/bills/b1/split", "/bills/b1/review", "/share", "/"])
      expect(MID_SEND.test(path), path).toBe(false);
    const before = window.location.pathname;
    window.history.pushState({}, "", "/bills/b1/share");
    expect(busyNow()).toBe(true);
    window.history.pushState({}, "", "/bills/b1");
    expect(busyNow()).toBe(false);
    window.history.pushState({}, "", before);
  });
});
