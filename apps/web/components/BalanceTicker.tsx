import { formatMoney } from "@/lib/format";
import { strings } from "@/lib/strings";

export type BalanceKind = "owed" | "owing" | "credit";

export type BalanceLine = {
  id: string;
  from: string;
  to: string;
  amountCents: number;
  /** owed = coming to you, owing = you owe (warn), credit = overpaid (good). */
  kind: BalanceKind;
};

export type BalanceTickerProps = {
  lines: readonly BalanceLine[];
  footnote?: string;
  currency?: string;
};

/** Mono rows "ARJUN → YOU $33.91" (PRD §14B). Direction is in the words, not just color. */
export function BalanceTicker({ lines, footnote, currency = "USD" }: BalanceTickerProps) {
  return (
    <div className="kt-ticker">
      <ul className="kt-ticker__list">
        {lines.map((line) => (
          <li key={line.id} className="kt-ticker__line" data-kind={line.kind}>
            <span>
              {line.from.toUpperCase()} <span aria-hidden="true">→</span>
              <span className="sr-only">{strings.balance.to}</span> {line.to.toUpperCase()}
            </span>
            <span className="kt-money">{formatMoney(line.amountCents, currency)}</span>
          </li>
        ))}
      </ul>
      {footnote ? <p className="kt-ticker__note">{footnote}</p> : null}
    </div>
  );
}
