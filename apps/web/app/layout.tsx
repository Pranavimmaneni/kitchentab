import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";
import { Space_Mono, Work_Sans } from "next/font/google";
import type { ReactNode } from "react";
import { ICONS, themeViewport } from "@/lib/pwa";
import { strings } from "@/lib/strings";
import { ThemeScript } from "@/components/ThemeScript";
import { PwaRoot } from "@/components/pwa/PwaRoot";
import { Providers } from "./providers";
import "./globals.css";

// Self-hosted at build time by next/font (PRD §14 Type).
const spaceMono = Space_Mono({
  subsets: ["latin"],
  weight: ["400", "700"],
  display: "swap",
  variable: "--font-space-mono",
});
const workSans = Work_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  variable: "--font-work-sans",
});

export const metadata: Metadata = {
  title: { default: strings.app.name, template: `%s · ${strings.app.name}` },
  description: strings.app.description,
  applicationName: strings.app.name,
  appleWebApp: { capable: true, title: strings.app.name, statusBarStyle: "default" },
  icons: {
    icon: [
      { url: ICONS.any192, sizes: "192x192", type: "image/png" },
      { url: ICONS.any512, sizes: "512x512", type: "image/png" },
    ],
    apple: [{ url: ICONS.apple180, sizes: "180x180", type: "image/png" }],
  },
  formatDetection: { telephone: false },
};

export const viewport: Viewport = themeViewport;

export default async function RootLayout({ children }: { children: ReactNode }) {
  // The per-request CSP nonce from middleware.ts (SEC-03). Reading headers also renders every page
  // per request, so no prerendered HTML ever ships without its nonce.
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html
      lang="en"
      className={`${spaceMono.variable} ${workSans.variable}`}
      suppressHydrationWarning
    >
      <head>
        <ThemeScript nonce={nonce} />
      </head>
      <body>
        <Providers>
          {children}
          <PwaRoot />
        </Providers>
      </body>
    </html>
  );
}
