import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { strings } from "@/lib/strings";
import { Gallery } from "./Gallery";

// Read the flag per request so `KT_DEV_COMPONENTS=1 next start` works on any build.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: strings.dev.title,
  robots: { index: false, follow: false },
};

/** Component gallery in both themes (PRD §14B). 404 in production unless KT_DEV_COMPONENTS=1. */
export default function DevComponentsPage() {
  if (process.env.NODE_ENV === "production" && process.env.KT_DEV_COMPONENTS !== "1") {
    notFound();
  }
  return <Gallery />;
}
