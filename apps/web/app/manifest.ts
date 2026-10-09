import type { MetadataRoute } from "next";
import { buildManifest } from "@/lib/pwa";

// Served at /manifest.webmanifest. The two narrow screenshots (Kitchen, Split) are added with
// the install flows in Milestone 9.
export default function manifest(): MetadataRoute.Manifest {
  return buildManifest();
}
