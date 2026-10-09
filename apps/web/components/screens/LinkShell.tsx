import type { ReactNode } from "react";
import { ThemeToggle } from "@/components/ThemeToggle";
import { strings } from "@/lib/strings";

/** Frame of a link page: "OPENED IN YOUR BROWSER · NO APP" · theme toggle, then content. */
export function LinkShell({ children }: { children: ReactNode }) {
  return (
    <div className="kt-screen">
      <div className="kt-topbar">
        <span className="kt-kicker">{strings.join.topRow}</span>
        <ThemeToggle />
      </div>
      <main className="kt-screen__main">{children}</main>
    </div>
  );
}
