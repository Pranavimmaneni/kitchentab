import { formatMoney } from "@/lib/format";
import { strings } from "@/lib/strings";
import { MemberAvatar } from "./MemberAvatar";
import { StampChip } from "./StampChip";

export type PersonShareCardProps = {
  name: string;
  colorIndex: number;
  itemCount: number;
  amountCents: number;
  /** Comma-separated item list, e.g. "Whole milk, Eggs, Basmati rice". */
  items: string;
  variant?: "payer" | "member" | "guest";
  currency?: string;
};

/** One person's share on "Here's the damage": avatar 36, name, count, amount, dashed rule, items. */
export function PersonShareCard({
  name,
  colorIndex,
  itemCount,
  amountCents,
  items,
  variant = "member",
  currency = "USD",
}: PersonShareCardProps) {
  return (
    <article className="kt-card kt-person-share" data-variant={variant}>
      <div className="kt-person-share__top">
        <MemberAvatar
          name={name}
          colorIndex={colorIndex}
          size={36}
          placeholder={variant === "guest"}
        />
        <div className="kt-person-share__who">
          <span className="kt-person-share__name">{name}</span>
          <span className="kt-person-share__count">
            {strings.people.itemCount(itemCount)}
            {variant === "payer" ? ` · ${strings.people.paidTheStore}` : null}
          </span>
        </div>
        {variant === "guest" ? (
          <StampChip tone="muted" rotate={-2}>
            {strings.people.guest}
          </StampChip>
        ) : null}
        <span className="kt-person-share__amount kt-money">
          {formatMoney(amountCents, currency)}
        </span>
      </div>
      <hr className="kt-rule" />
      <p className="kt-person-share__items">{items}</p>
    </article>
  );
}
