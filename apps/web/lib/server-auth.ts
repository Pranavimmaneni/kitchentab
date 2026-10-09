// Server-only helpers for pages that branch on sign-in state.
import { isApiError } from "./api/client";
import { hasSessionCookie, serverGet } from "./api/server";
import type { AuthMethods, Me } from "./api/types";

/** The signed-in user, or null (no cookie, expired session, API down). */
export async function serverMe(): Promise<Me | null> {
  if (!(await hasSessionCookie())) return null;
  try {
    return await serverGet<Me>("/me");
  } catch (error) {
    if (isApiError(error)) return null;
    throw error;
  }
}

export const EMAIL_ONLY: AuthMethods = {
  email: true,
  phone: false,
  google: false,
  turnstile_site_key: null,
};

/** Configured sign-in methods; email only when the API can't be reached (BUILD_ALL C3). */
export async function serverMethods(): Promise<AuthMethods> {
  try {
    return await serverGet<AuthMethods>("/auth/methods", { forwardCookies: false });
  } catch {
    return EMAIL_ONLY;
  }
}
