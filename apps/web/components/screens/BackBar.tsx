import Link from "next/link";
import { BackIcon } from "@/components/icons";
import { ThemeToggle } from "@/components/ThemeToggle";
import { strings } from "@/lib/strings";

/** Header with only a 44 px back link and the theme toggle (Verify.dc.html). */
export function BackBar({ href }: { href: string }) {
  return (
    <header className="kt-app-header" data-back="">
      <div className="kt-app-header__row">
        <Link href={href} aria-label={strings.header.back} className="kt-app-header__back">
          <BackIcon size={22} />
        </Link>
        <span className="kt-app-header__titles" />
        <ThemeToggle />
      </div>
    </header>
  );
}
