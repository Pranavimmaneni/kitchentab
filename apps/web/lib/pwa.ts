import type { MetadataRoute, Viewport } from "next";
import { strings } from "./strings";

/** `<meta name="theme-color">` per color scheme (PRD §20.1, PWA-10). */
export const themeViewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ECEAE2" },
    { media: "(prefers-color-scheme: dark)", color: "#0D0D0B" },
  ],
};

export const ICONS = {
  any192: "/icons/icon-192.png",
  any512: "/icons/icon-512.png",
  maskable512: "/icons/icon-maskable-512.png",
  apple180: "/icons/apple-touch-icon.png",
} as const;

/** Web app manifest (PRD §20.1). Screenshots arrive with install flows in Milestone 9. */
export function buildManifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    start_url: "/?source=pwa",
    scope: "/",
    name: strings.app.name,
    short_name: strings.app.name,
    description: strings.app.description,
    display: "standalone",
    display_override: ["standalone"],
    orientation: "portrait",
    background_color: "#ECEAE2",
    theme_color: "#121210",
    icons: [
      { src: ICONS.any192, sizes: "192x192", type: "image/png", purpose: "any" },
      { src: ICONS.any512, sizes: "512x512", type: "image/png", purpose: "any" },
      { src: ICONS.maskable512, sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    shortcuts: [
      { name: strings.header.snapReceipt, short_name: strings.header.snapShort, url: "/bills/new" },
      { name: strings.kitchen.fridge, url: "/kitchen/fridge" },
      { name: strings.kitchen.asks, url: "/kitchen/requests" },
    ],
  };
}
