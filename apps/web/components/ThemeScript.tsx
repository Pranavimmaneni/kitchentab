import { THEME_SCRIPT } from "@/lib/theme-script";

/**
 * The exact inline no-flash script, rendered in <head>. Its SHA-256 is THEME_SCRIPT_HASH in
 * lib/csp.ts (ADR-0007); tests/unit/theme-script.test.tsx renders this component and checks the
 * hash, so the header and the script cannot drift.
 */
export function ThemeScript({ nonce }: { nonce?: string }) {
  return (
    <script
      id="kt-theme-script"
      nonce={nonce}
      // Browsers hide a script's nonce from the DOM once it ran, so the client sees nonce="".
      suppressHydrationWarning
      dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }}
    />
  );
}
