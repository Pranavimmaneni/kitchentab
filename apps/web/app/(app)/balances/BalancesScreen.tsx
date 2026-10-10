"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { BottomNav } from "@/components/BottomNav";
import { MemberAvatar } from "@/components/MemberAvatar";
import { PillButton } from "@/components/PillButton";
import { Skeleton } from "@/components/Skeleton";
import { StampChip } from "@/components/StampChip";
import { Toast } from "@/components/Toast";
import { ErrorCard } from "@/components/screens/ErrorCard";
import { HouseGate, type HouseContext } from "@/components/screens/HouseGate";
import { HouseHeader } from "@/components/screens/HouseHeader";
import { api, isApiError, newId } from "@/lib/api/client";
import { useBalances } from "@/lib/api/tabs-hooks";
import type { BalancePair } from "@/lib/api/tabs-types";
import { colorIndexOf } from "@/lib/bills";
import { formatMoney } from "@/lib/format";
import { strings } from "@/lib/strings";
import { balanceHeadline, monDay, signedMoney } from "@/lib/tabs";

/** Balances (PRD §14A): each pair netted across open tabs and carried entries (FR-50, FR-51). */
export function BalancesScreen() {
  return (
    <HouseGate title={strings.balancesScreen.title}>{(ctx) => <Balances {...ctx} />}</HouseGate>
  );
}

function Balances({ house, houses, select }: HouseContext) {
  const balances = useBalances(house.id);
  const [toast, setToast] = useState<{ message: string; variant?: "info" | "error" } | null>(null);
  const data = balances.data;
  const headline = data ? balanceHeadline(data.net_cents) : null;
  return (
    <div className="kt-screen kt-balances">
      <HouseHeader
        house={house}
        houses={houses}
        onSelect={select}
        title={strings.balancesScreen.title}
      />
      <main className="kt-screen__main">
        {balances.isError ? (
          <ErrorCard error={balances.error} onRetry={() => void balances.refetch()} />
        ) : null}
        {!data && !balances.isError ? <Skeleton variant="card" lines={4} /> : null}
        {headline ? (
          <section className="kt-balance-card" aria-label={strings.tabs.balance}>
            <div className="kt-balance-card__big" data-kind={headline.kind}>
              {headline.kind === "square" ? (
                <StampChip rotate={-3} large>
                  {headline.text}
                </StampChip>
              ) : (
                <>
                  <span className="kt-balance-card__amount">{headline.text}</span>
                  <span className="kt-balance-card__words">
                    {headline.kind === "owed" ? strings.tabs.comingToYou : strings.tabs.youOwe}
                  </span>
                </>
              )}
            </div>
            <p className="kt-ticker__note">{strings.balancesScreen.netted}</p>
          </section>
        ) : null}
        {data && data.pairs.length === 0 ? (
          <p className="kt-footnote">{strings.balancesScreen.empty}</p>
        ) : null}
        {data && data.pairs.length > 0 ? (
          <ul className="kt-list kt-pairs">
            {data.pairs.map((pair) => (
              <li key={pair.member_id}>
                <PairRow
                  pair={pair}
                  onToast={(message, variant) => setToast({ message, variant })}
                />
              </li>
            ))}
          </ul>
        ) : null}
      </main>
      <BottomNav />
      <Toast
        message={toast?.message ?? null}
        variant={toast?.variant}
        onDismiss={() => setToast(null)}
      />
    </div>
  );
}

function PairRow({
  pair,
  onToast,
}: {
  pair: BalancePair;
  onToast: (message: string, variant?: "info" | "error") => void;
}) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [reminding, setReminding] = useState(false);
  const owesMe = pair.net_cents > 0;
  const iOwe = pair.net_cents < 0;
  const line = owesMe
    ? strings.balancesScreen.owesYou(pair.display_name)
    : iOwe
      ? strings.balancesScreen.youOwe(pair.display_name)
      : strings.balancesScreen.square(pair.display_name);
  const remindBills = pair.bills.filter((b) => b.cents > 0);
  const payBill = [...pair.bills].filter((b) => b.cents < 0).sort((a, b) => a.cents - b.cents)[0];

  async function remind() {
    setReminding(true);
    try {
      for (const bill of remindBills) {
        await api.post(
          `/bills/${bill.bill_id}/remind`,
          { member_id: pair.member_id },
          { idempotencyKey: newId() },
        );
      }
      onToast(strings.balancesScreen.reminded(pair.display_name));
    } catch (error) {
      onToast(isApiError(error) ? error.title : strings.error.title, "error");
    } finally {
      setReminding(false);
    }
  }

  return (
    <div className="kt-card kt-pair" data-kind={owesMe ? "owed" : iOwe ? "owing" : "square"}>
      <div className="kt-pair__row">
        <button
          type="button"
          className="kt-pair__toggle"
          aria-expanded={open}
          aria-controls={listId}
          onClick={() => setOpen((v) => !v)}
        >
          <MemberAvatar
            name={pair.display_name}
            initial={pair.initial}
            colorIndex={colorIndexOf({ color: pair.color, sort_order: 0 })}
            size={30}
          />
          <span className="kt-pair__line">{line}</span>
          <span className="kt-pair__amount kt-money">{formatMoney(Math.abs(pair.net_cents))}</span>
        </button>
        {owesMe && remindBills.length > 0 ? (
          <PillButton
            variant="secondary"
            size={44}
            loading={reminding}
            onClick={() => void remind()}
          >
            {strings.balancesScreen.remind}
          </PillButton>
        ) : null}
        {iOwe && payBill ? (
          <PillButton variant="primary" size={44} href={`/bills/${payBill.bill_id}`}>
            {strings.balancesScreen.pay}
          </PillButton>
        ) : null}
      </div>
      {open ? (
        <div id={listId} className="kt-pair__bills">
          <span className="kt-field__label">{strings.balancesScreen.fromTabs}</span>
          <ul className="kt-list">
            {pair.bills.map((bill) => (
              <li key={bill.bill_id}>
                <Link href={`/bills/${bill.bill_id}`} className="kt-pair__bill">
                  <span>
                    {bill.store_name ?? strings.bill.untitled} · {monDay(bill.purchase_date)}
                  </span>
                  <span className="kt-money" data-kind={bill.cents < 0 ? "owing" : "owed"}>
                    {signedMoney(bill.cents)}
                  </span>
                </Link>
              </li>
            ))}
            {pair.carried_cents !== 0 ? (
              <li className="kt-pair__bill" data-carried="">
                <span>{strings.balancesScreen.carried}</span>
                <span className="kt-money" data-kind={pair.carried_cents < 0 ? "owing" : "owed"}>
                  {signedMoney(pair.carried_cents)}
                </span>
              </li>
            ) : null}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
