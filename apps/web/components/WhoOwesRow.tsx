import type { ReactNode } from "react";
import { formatMoney } from "@/lib/format";
import { strings } from "@/lib/strings";
import { BellIcon } from "./icons";
import { MemberAvatar } from "./MemberAvatar";

export type OwesStatus = "unpaid" | "pending" | "paid" | "credit" | "payer";

export type WhoOwesRowProps = {
  name: string;
  colorIndex: number;
  amountCents: number;
  status: OwesStatus;
  /** Amount the status line refers to (paid amount, pending amount or credit). */
  statusAmountCents?: number;
  /** Payer's bell nudge on unpaid rows. */
  onNudge?: () => void;
  /** Custom action (Confirm / Reject, Pay). Replaces the bell. */
  action?: ReactNode;
  currency?: string;
};

function statusText(status: OwesStatus, amount: string): string {
  switch (status) {
    case "unpaid":
      return strings.owes.notPaid;
    case "pending":
      return strings.owes.pending(amount);
    case "paid":
      return strings.owes.paid(amount);
    case "credit":
      return strings.owes.credit(amount);
    case "payer":
      return strings.owes.paidTheStore;
  }
}

/** Avatar 30, name + status line (in words, not color only), amount, action (PRD §14B). */
export function WhoOwesRow({
  name,
  colorIndex,
  amountCents,
  status,
  statusAmountCents = amountCents,
  onNudge,
  action,
  currency = "USD",
}: WhoOwesRowProps) {
  return (
    <li className="kt-owes-row" data-status={status}>
      <MemberAvatar name={name} colorIndex={colorIndex} size={30} />
      <div className="kt-owes-row__who">
        <span className="kt-owes-row__name">{name}</span>
        <span className="kt-owes-row__status">{statusText(status, formatMoney(statusAmountCents, currency))}</span>
      </div>
      <span className="kt-owes-row__amount kt-money">{formatMoney(amountCents, currency)}</span>
      <span className="kt-owes-row__action">
        {action ??
          (onNudge && status === "unpaid" ? (
            <button type="button" className="kt-round-button" aria-label={strings.owes.nudge(name)} onClick={onNudge}>
              <BellIcon size={18} />
            </button>
          ) : null)}
      </span>
    </li>
  );
}
