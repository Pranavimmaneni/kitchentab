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
