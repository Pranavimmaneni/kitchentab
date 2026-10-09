import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { serverMe, serverMethods } from "@/lib/server-auth";
import { StartScreen } from "./StartScreen";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Start a house" };

/** A · Start a house (PRD §14A). Already signed in → `/`. */
export default async function StartPage() {
  const me = await serverMe();
  if (me) redirect("/");
  const methods = await serverMethods();
  return <StartScreen methods={methods} />;
}
