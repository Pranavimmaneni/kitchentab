"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { strings } from "@/lib/strings";
import { HouseIcon, KitchenIcon, ReceiptIcon, ScaleIcon } from "./icons";

export const NAV_ITEMS = [
  { href: "/", label: strings.nav.kitchen, Icon: KitchenIcon },
  { href: "/tabs", label: strings.nav.tabs, Icon: ReceiptIcon },
  { href: "/balances", label: strings.nav.balances, Icon: ScaleIcon },
  { href: "/house", label: strings.nav.house, Icon: HouseIcon },
] as const;

export type BottomNavProps = {
  /** Overrides the current path (demos). */
  current?: string;
  /** Stick to the bottom of the viewport (default) or stay in flow. */
  sticky?: boolean;
};

function isActive(href: string, path: string): boolean {
  if (href === "/") return path === "/" || path.startsWith("/kitchen");
  return path === href || path.startsWith(`${href}/`);
}

/** KITCHEN · TABS · BALANCES · HOUSE, 50 px targets, lime pill on the active item (PRD §19.8). */
export function BottomNav({ current, sticky = true }: BottomNavProps) {
  const pathname = usePathname();
  const path = current ?? pathname ?? "/";
  return (
    <nav className="kt-bottom-nav" aria-label={strings.nav.label} data-sticky={sticky || undefined}>
      {NAV_ITEMS.map(({ href, label, Icon }) => {
        const active = isActive(href, path);
        return (
          <Link
            key={href}
            href={href}
            className="kt-bottom-nav__item"
            aria-current={active ? "page" : undefined}
            prefetch={false}
          >
            <span className="kt-bottom-nav__icon">
              <Icon size={20} />
            </span>
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
