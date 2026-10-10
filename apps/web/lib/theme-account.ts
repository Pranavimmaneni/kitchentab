"use client";

import type { QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { api } from "./api/client";
import { keys } from "./api/hooks";
import type { Me, User } from "./api/types";
import { readPreference, setPreference, type ThemePreference } from "./theme";

/**
 * Theme preference on the account (PRD §14B ThemeToggle: "persists to account + localStorage").
 * The device copy applies instantly (no flash, works signed out); signed in, the choice is also
 * saved with `PATCH /me {theme}` (a set-state field on the user's own row) and the account value
 * wins when `/me` loads on another device.
 */
export async function saveThemeToAccount(
  client: QueryClient | undefined,
  preference: ThemePreference,
): Promise<void> {
  const me = client?.getQueryData<Me | null>(keys.me);
  if (!client || !me) return; // signed out (or not loaded): this device only
  client.setQueryData<Me | null>(keys.me, (old) =>
    old ? { ...old, user: { ...old.user, theme: preference } } : old,
  );
  try {
    const user = await api.request<User>("PATCH", "/me", { body: { theme: preference } });
    client.setQueryData<Me | null>(keys.me, (old) => (old ? { ...old, user: { ...old.user, ...user } } : old));
  } catch {
    // The device keeps the choice; the account catches up the next time it is saved.
  }
}

/** Applies the account's theme when `/me` loads or changes (another device saved one). */
export function useAccountTheme(theme: ThemePreference | undefined): void {
  useEffect(() => {
    if (!theme) return;
    if (readPreference() !== theme) setPreference(theme);
  }, [theme]);
}
