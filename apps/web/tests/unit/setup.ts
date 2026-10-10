import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// jsdom has no matchMedia. Default: light scheme, reduced motion (no animations in tests).
export const mediaState = { dark: false, reducedMotion: true };

function matches(query: string): boolean {
  if (query.includes("prefers-color-scheme: dark")) return mediaState.dark;
  if (query.includes("prefers-color-scheme: light")) return !mediaState.dark;
  if (query.includes("prefers-reduced-motion: no-preference")) return !mediaState.reducedMotion;
  if (query.includes("prefers-reduced-motion: reduce")) return mediaState.reducedMotion;
  return false;
}

// Server-side tests (`@vitest-environment node`, e.g. the Next route handlers) have no window.
if (typeof window !== "undefined")
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      get matches() {
        return matches(query);
      },
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });

afterEach(() => {
  if (typeof window === "undefined") return;
  cleanup();
  mediaState.dark = false;
  mediaState.reducedMotion = true;
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});
