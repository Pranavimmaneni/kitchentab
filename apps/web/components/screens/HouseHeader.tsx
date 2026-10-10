"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { BottomSheet } from "@/components/BottomSheet";
import { PillButton } from "@/components/PillButton";
import { ThemeToggle } from "@/components/ThemeToggle";
import { CheckIcon, PlusIcon } from "@/components/icons";
import type { MyHouse } from "@/lib/api/types";
import { forgetHouse } from "@/lib/house";
import { strings } from "@/lib/strings";

/**
 * Top row of the house landing screens (Kitchen.dc.html, Main.dc.html): "KITCHENTAB ** {HOUSE}"
 * (tap → house switcher when the user has 2+ houses, FR-08), a mono 26 title, extra actions and
 * the theme toggle.
 */
export function HouseHeader({
  house,
  houses,
  onSelect,
  title,
  actions,
}: {
  house: MyHouse;
  houses: readonly MyHouse[];
  onSelect: (id: string) => void;
  title: string;
  actions?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const brand = strings.tabs.houseSwitch(house.name);
  return (
    <header className="kt-app-header" data-large="">
      <div className="kt-app-header__row">
        <div className="kt-app-header__titles">
          {houses.length > 1 ? (
            <button
              type="button"
              className="kt-brand-switch"
              aria-haspopup="dialog"
              aria-label={`${brand}. ${strings.tabs.switchLabel}`}
              onClick={() => setOpen(true)}
            >
              {brand}
            </button>
          ) : (
            <span className="kt-app-header__step kt-brand-line">{brand}</span>
          )}
          <h1 className="kt-app-header__title">{title}</h1>
        </div>
        {actions}
        <ThemeToggle />
      </div>
      <HouseSwitcher
        open={open}
        onClose={() => setOpen(false)}
        houses={houses}
        currentId={house.id}
        onSelect={(id) => {
          onSelect(id);
          setOpen(false);
        }}
      />
    </header>
  );
}

/** "Sheet listing houses + Start another house" (PRD §14A House switcher). */
export function HouseSwitcher({
  open,
  onClose,
  houses,
  currentId,
  onSelect,
}: {
  open: boolean;
  onClose: () => void;
  houses: readonly MyHouse[];
  currentId: string;
  onSelect: (id: string) => void;
}) {
  const router = useRouter();
  return (
    <BottomSheet open={open} onClose={onClose} title={strings.switcher.title}>
      <ul className="kt-list kt-switcher">
        {houses.map((h) => {
          const current = h.id === currentId;
          return (
            <li key={h.id}>
              <button
                type="button"
                className="kt-switcher__house"
                aria-current={current ? "true" : undefined}
                onClick={() => {
                  onSelect(h.id);
                  router.refresh();
                }}
              >
                <span className="kt-switcher__name">{h.name}</span>
                {current ? (
                  <span className="kt-switcher__here">
                    <CheckIcon size={16} /> {strings.switcher.current}
                  </span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
      <PillButton
        variant="dashed"
        fullWidth
        icon={<PlusIcon size={18} />}
        onClick={() => {
          forgetHouse();
          router.push("/houses/new");
        }}
      >
        {strings.switcher.startAnother}
      </PillButton>
    </BottomSheet>
  );
}

/** 44 px accent scan button on the Kitchen header (opens the capture sheet). */
export function ScanLink({ houseId, children }: { houseId: string; children: ReactNode }) {
  return (
    <Link
      href={`/bills/new?house=${houseId}`}
      className="kt-icon-button"
      data-variant="accent"
      aria-label={strings.header.snapReceipt}
    >
      {children}
    </Link>
  );
}
