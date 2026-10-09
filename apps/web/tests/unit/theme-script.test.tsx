import { createHash } from "node:crypto";
import { fireEvent, render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ThemeScript } from "@/components/ThemeScript";
import { ThemeToggle } from "@/components/ThemeToggle";
import { THEME_SCRIPT_HASH, scriptSrc } from "@/lib/csp";
import { setPreference } from "@/lib/theme";
import { THEME_SCRIPT, THEME_STORAGE_KEY } from "@/lib/theme-script";
import { mediaState } from "./setup";

function runScript(): void {
  // Same text the browser executes from the inline <script>.
  new Function(THEME_SCRIPT)();
}

describe("no-flash theme script", () => {
  it("the CSP hash matches the exact inline script that is rendered", () => {
    const markup = renderToStaticMarkup(<ThemeScript />);
    const inline = /^<script[^>]*>([\s\S]*)<\/script>$/.exec(markup)?.[1];
    expect(inline).toBe(THEME_SCRIPT);
    const recomputed = `'sha256-${createHash("sha256").update(inline!, "utf8").digest("base64")}'`;
    expect(THEME_SCRIPT_HASH).toBe(recomputed);
    expect(scriptSrc()).toBe(`script-src 'self' ${recomputed}`);
  });

  it("applies a saved light or dark choice to <html> before paint", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    runScript();
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    runScript();
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("leaves the attribute off for system or junk values", () => {
    document.documentElement.setAttribute("data-theme", "dark");
    window.localStorage.setItem(THEME_STORAGE_KEY, "purple");
    runScript();
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    window.localStorage.removeItem(THEME_STORAGE_KEY);
    runScript();
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });
});

describe("ThemeToggle", () => {
  it("in light shows the moon and offers dark; clicking persists the choice", () => {
    render(<ThemeToggle />);
    const button = screen.getByRole("button", { name: "Switch to dark mode" });
    expect(button.querySelector('[data-icon="moon"]')).not.toBeNull();
    fireEvent.click(button);
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    const after = screen.getByRole("button", { name: "Switch to light mode" });
    expect(after.querySelector('[data-icon="sun"]')).not.toBeNull();
  });

  it("follows the system scheme when nothing is saved", () => {
    mediaState.dark = true;
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: "Switch to light mode" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("system preference clears the attribute and the saved value", () => {
    setPreference("dark");
    setPreference("system");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });
});
