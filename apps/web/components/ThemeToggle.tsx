"use client";

import { QueryClientContext } from "@tanstack/react-query";
import { useContext } from "react";
import { setPreference, useTheme, type Theme } from "@/lib/theme";
import { saveThemeToAccount } from "@/lib/theme-account";
import { strings } from "@/lib/strings";
import { MoonIcon, SunIcon } from "./icons";

export type ThemeToggleProps = {
  /** Offset sticker shadow variant (as next to the scan button on the Kitchen header). */
  withShadow?: boolean;
  /**
   * Shows the toggle as it looks in a given theme regardless of the page theme. Only for demos
   * inside a `data-theme` panel; clicking still switches the page theme.
   */
  previewTheme?: Theme;
};

/**
 * 44 px square, 2 px border, radius 12. Moon in light ("Switch to dark mode"), sun in dark.
 * Saves to this device at once and, when signed in, to the account (`PATCH /me`).
 */
export function ThemeToggle({ withShadow = false, previewTheme }: ThemeToggleProps) {
  const { theme: pageTheme } = useTheme();
  // Optional: the toggle also renders outside a QueryClientProvider (component demos, tests).
  const queryClient = useContext(QueryClientContext);
  const shown = previewTheme ?? pageTheme;
  const next: Theme = pageTheme === "dark" ? "light" : "dark";
  const label = shown === "dark" ? strings.theme.toLight : strings.theme.toDark;
  return (
    <button
      type="button"
      className="kt-icon-button kt-theme-toggle"
      data-shadow={withShadow || undefined}
      aria-label={label}
      onClick={() => {
        setPreference(next);
        void saveThemeToAccount(queryClient, next);
      }}
    >
      {shown === "dark" ? <SunIcon size={18} /> : <MoonIcon size={18} />}
    </button>
  );
}
