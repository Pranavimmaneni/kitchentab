"use client";

import { AppHeader } from "@/components/AppHeader";
import { PillButton } from "@/components/PillButton";
import { ReceiptCard } from "@/components/ReceiptCard";
import { WhoOwesRow } from "@/components/WhoOwesRow";
import { BillGate } from "@/components/screens/BillGate";
import type { BillView } from "@/lib/api/types";
import { colorIndexOf, displayName, shortDate } from "@/lib/bills";
import { formatMoney } from "@/lib/format";
import { strings } from "@/lib/strings";

export function BillRoute({ id }: { id: string }) {
  return (
    <BillGate id={id} title={strings.bill.untitled}>
      {(view) => <BillScreen view={view} />}
    </BillGate>
  );
}

/**
 * Open bill, read view only (screen 5 arrives with Milestone 6): store, total and who owes what
 * from the server's shares.
 */
function BillScreen({ view }: { view: BillView }) {
  const meId = view.me.member_id;
  const currency = view.bill.currency;
  const store = view.bill.store_name ?? strings.bill.untitled;
  const payer = view.participants.find((p) => p.id === view.bill.payer_member_id);
  const rows = view.participants.filter((p) => (view.shares[p.key] ?? 0) !== 0 || p.id === view.bill.payer_member_id);
  return (
    <div className="kt-screen">
      <AppHeader
        title={store}
        subtitle={strings.bill.subtitle(
          shortDate(view.bill.purchase_date),
          payer ? displayName(payer, meId) : "",
          formatMoney(view.bill.total_cents, currency),
        )}
        back={{ href: "/" }}
      />
      <main className="kt-screen__main">
        {view.bill.state === "draft" && view.me.can_edit ? (
          <PillButton href={`/bills/${view.bill.id}/review`} variant="dashed" fullWidth>
            {strings.bill.draft(store)}
          </PillButton>
        ) : null}
        <ReceiptCard heading={strings.owes.heading}>
          <ul className="kt-owes-list">
            {rows.map((p) => (
              <WhoOwesRow
                key={p.key}
                name={displayName(p, meId)}
                initial={p.initial}
                colorIndex={colorIndexOf(p)}
                amountCents={view.shares[p.key] ?? 0}
                status={p.id === view.bill.payer_member_id ? "payer" : "unpaid"}
                currency={currency}
              />
            ))}
          </ul>
        </ReceiptCard>
      </main>
    </div>
  );
}
