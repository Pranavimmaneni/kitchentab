"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { PersonShareCard } from "@/components/PersonShareCard";
import { PillButton } from "@/components/PillButton";
import { ReceiptCard } from "@/components/ReceiptCard";
import { ActionBar } from "@/components/screens/ActionBar";
import { BillGate } from "@/components/screens/BillGate";
import { ScreenToast } from "@/components/screens/ScreenToast";
import { StockingCard, type StockingHandle } from "@/features/kitchen/stocking/StockingCard";
import { useToast } from "@/components/screens/useToast";
import { api, isApiError, newId } from "@/lib/api/client";
import { keys, useHouse } from "@/lib/api/hooks";
import type { BillView, SendOut } from "@/lib/api/types";
import { actionKey, clearActionKey } from "@/lib/auth-flow";
import { colorIndexOf, displayName, longDate } from "@/lib/bills";
import { formatMoney } from "@/lib/format";
import { strings } from "@/lib/strings";

export function SendRoute({ id }: { id: string }) {
  return (
    <BillGate id={id} title={strings.send.title}>
      {(view) => <SendScreen view={view} />}
    </BillGate>
  );
}

/** People on the bill with their items, in column order (server shares; FR-30). */
export function peopleOf(view: BillView) {
  return view.participants
    .map((p) => {
      const items = view.items.filter((i) => i.allocations.some((a) => a.participant === p.key));
      return { participant: p, items, amount: view.shares[p.key] ?? 0 };
    })
    .filter((x) => x.items.length > 0);
}

function SendScreen({ view }: { view: BillView }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const billId = view.bill.id;
  const meId = view.me.member_id;
  const house = useHouse(view.bill.house_id);
  const { toast, show, dismiss } = useToast();
  const [sending, setSending] = useState(false);
  const stocking = useRef<StockingHandle>(null);
  const currency = view.bill.currency;

  useEffect(() => {
    if (view.bill.state === "open") router.replace(`/bills/${billId}/share`);
    else if (view.bill.state !== "draft") router.replace(`/bills/${billId}`);
  }, [view.bill.state, billId, router]);

  const people = peopleOf(view);
  const payer = view.participants.find((p) => p.id === view.bill.payer_member_id);
  const payerName = payer ? displayName(payer, meId) : "";
  const payerShare = payer ? (view.shares[payer.key] ?? 0) : 0;
  const comingBack = view.bill.total_cents - payerShare;
  const left = view.progress.total - view.progress.assigned;
  const houseName = house.data?.house.name ?? "";

  async function send(): Promise<void> {
    if (sending) return;
    setSending(true);
    // One key per bill: a double tap or a retry after a lost response returns the first result.
    const keyName = `send:${billId}`;
    // The food flies into its bins while the request is in flight (FR-G10); the Share screen
    // opens when both are done (ANIM-02).
    const animation = stocking.current?.play() ?? Promise.resolve();
    try {
      await api.post<SendOut>(`/bills/${billId}/send`, undefined, { idempotencyKey: actionKey(keyName, newId) });
      clearActionKey(keyName);
      await animation;
      await queryClient.invalidateQueries({ queryKey: keys.bill(billId) });
      router.push(`/bills/${billId}/share`);
    } catch (error) {
      // Nothing was stocked: the icons go home.
      await stocking.current?.flyBack();
      setSending(false);
      show({ message: isApiError(error) ? error.title : strings.error.couldntSave, variant: "error" });
    }
  }

  return (
    <div className="kt-screen">
      <AppHeader stepLabel={strings.send.step} title={strings.send.title} back={{ href: `/bills/${billId}/split` }} />
      <main className="kt-screen__main">
        <ReceiptCard heading={view.bill.store_name ?? strings.bill.untitled} hideHeading>
          <div className="kt-summary">
            <div className="kt-summary__top">
              <span>{view.bill.store_name ?? strings.bill.untitled}</span>
              <span className="kt-money">{formatMoney(view.bill.total_cents, currency)}</span>
            </div>
            <p className="kt-summary__meta">
              {strings.send.summary(longDate(view.bill.purchase_date), payerName, view.items.length)}
            </p>
          </div>
        </ReceiptCard>
        <ul className="kt-people">
          {people.map(({ participant, items, amount }) => (
            <li key={participant.key}>
              <PersonShareCard
                name={displayName(participant, meId)}
                initial={participant.initial}
                colorIndex={colorIndexOf(participant)}
                itemCount={items.length}
                amountCents={amount}
                items={items.map((i) => i.name).join(", ")}
                variant={participant.kind === "guest" ? "guest" : "member"}
                currency={currency}
              />
            </li>
          ))}
        </ul>
        <StockingCard ref={stocking} items={view.items} currency={currency} />
        {houseName ? (
          <p className="kt-mono-note">
            {strings.send.noteOpen}
            <br />
            {strings.send.noteHop(houseName)}
          </p>
        ) : null}
      </main>
      <ActionBar>
        {view.me.is_payer ? (
          <div className="kt-action-bar__row">
            <span>{strings.send.comingBack}</span>
            <span className="kt-highlight kt-money">{formatMoney(comingBack, currency)}</span>
          </div>
        ) : null}
        <PillButton
          fullWidth
          loading={sending}
          disabled={left > 0}
          disabledReason={left > 0 ? strings.split.itemsLeft(left) : undefined}
          onClick={() => void send()}
        >
          {strings.send.cta}
        </PillButton>
      </ActionBar>
      <ScreenToast toast={toast} onDismiss={dismiss} />
    </div>
  );
}
