import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { hasSessionCookie } from "@/lib/api/server";
import { AuthGate } from "./AuthGate";

/** Signed-in screens. No session cookie → A (`/start`); an expired session is caught client-side. */
export default async function SignedInLayout({ children }: { children: ReactNode }) {
  if (!(await hasSessionCookie())) redirect("/start");
  return <AuthGate>{children}</AuthGate>;
}
