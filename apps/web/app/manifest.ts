import type { MetadataRoute } from "next";
import { buildManifest } from "@/lib/pwa";

// Served at /manifest.webmanifest (PRD §20.1, PWA-01).
export default function manifest(): MetadataRoute.Manifest {
  return buildManifest();
}
