"use client";

import { useSyncExternalStore } from "react";
import { THEME_STORAGE_KEY } from "./theme-script";

export type ThemePreference = "system" | "light" | "dark";
export type Theme = "light" | "dark";

/** `<meta name="theme-color">` values per scheme (PRD §20.1). */
export const THEME_COLORS: Record<Theme, string> = { light: "#ECEAE2", dark: "#0D0D0B" };

const DARK_QUERY = "(prefers-color-scheme: dark)";
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

function systemTheme(): Theme {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return "light";
  return window.matchMedia(DARK_QUERY).matches ? "dark" : "light";
}

export function readPreference(): ThemePreference {
  try {
    const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
    return saved === "light" || saved === "dark" ? saved : "system";
  } catch {
    return "system";
  }
}

export function resolveTheme(preference: ThemePreference): Theme {
  return preference === "system" ? systemTheme() : preference;
}

/** Points every theme-color meta at the forced theme, or restores the per-scheme values. */
function syncThemeColorMeta(preference: ThemePreference): void {
  document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]').forEach((meta) => {
    if (meta.dataset.ktOriginal === undefined) meta.dataset.ktOriginal = meta.content;
    meta.content =
      preference === "system" ? meta.dataset.ktOriginal : THEME_COLORS[preference];
  });
}

/** Applies a preference to <html> and saves it on this device (account sync arrives later). */
export function setPreference(preference: ThemePreference): void {
  const root = document.documentElement;
  if (preference === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", preference);
  try {
    if (preference === "system") window.localStorage.removeItem(THEME_STORAGE_KEY);
    else window.localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // Storage can be unavailable (private mode); the attribute still applies for this visit.
  }
  syncThemeColorMeta(preference);
  emit();
}

/** Re-applies the saved preference after hydration (keeps theme-color meta in step). */
export function initTheme(): () => void {
  syncThemeColorMeta(readPreference());
  if (typeof window.matchMedia !== "function") return () => undefined;
  const media = window.matchMedia(DARK_QUERY);
  media.addEventListener("change", emit);
  return () => media.removeEventListener("change", emit);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const media =
    typeof window.matchMedia === "function" ? window.matchMedia(DARK_QUERY) : undefined;
  media?.addEventListener("change", listener);
  return () => {
    listeners.delete(listener);
    media?.removeEventListener("change", listener);
  };
}

function snapshot(): string {
  const preference = readPreference();
  return `${preference}:${resolveTheme(preference)}`;
}

/** Current preference and the theme it resolves to. Server render assumes system/light. */
export function useTheme(): { preference: ThemePreference; theme: Theme } {
  const value = useSyncExternalStore(subscribe, snapshot, () => "system:light");
  const [preference, theme] = value.split(":") as [ThemePreference, Theme];
  return { preference, theme };
}
