"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { AppHeader } from "@/components/AppHeader";
import { BottomSheet } from "@/components/BottomSheet";
import { PillButton } from "@/components/PillButton";
import { ProgressBar } from "@/components/ProgressBar";
import { SplitGrid, type SplitMember } from "@/components/SplitGrid";
import { BillGate } from "@/components/screens/BillGate";
import { ScreenToast } from "@/components/screens/ScreenToast";
import { useToast } from "@/components/screens/useToast";
import { api, isApiError, newId } from "@/lib/api/client";
import { keys } from "@/lib/api/hooks";
import type { BillView, FillRestOut, GuestOut } from "@/lib/api/types";
import {
  assignedMap,
  colorIndexOf,
  displayName,
  itemNote,
  participantTag,
  previewShares,
  shortDate,
  splitColumns,
} from "@/lib/bills";
import { formatMoney } from "@/lib/format";
import { copyText, displayLink, shareOrCopy } from "@/lib/share";
import { inverseChanges, popUndo, pushUndo, type CellChange, type SplitAction } from "@/lib/split/undo";
import { useSplitWrites } from "@/lib/split/useSplitWrites";
import { strings } from "@/lib/strings";

export function SplitRoute({ id }: { id: string }) {
  return (
    <BillGate id={id} title={strings.split.title}>
      {(view) => <SplitScreen view={view} />}
    </BillGate>
  );
}

function SplitScreen({ view }: { view: BillView }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const billId = view.bill.id;
  const meId = view.me.member_id;
  const currency = view.bill.currency;
  const { toast, show, dismiss } = useToast();
  const [undo, setUndo] = useState<SplitAction[]>([]);
  const [filling, setFilling] = useState(false);
  const [guestOpen, setGuestOpen] = useState(false);
  const [guestName, setGuestName] = useState("");
  const [guestBusy, setGuestBusy] = useState(false);
  const [guest, setGuest] = useState<GuestOut | null>(null);
  const [guestError, setGuestError] = useState<string | null>(null);
  const guestId = useId();

  const isPayer = view.me.is_payer || view.me.can_edit;
  useEffect(() => {
    if (view.bill.state !== "draft") router.replace(`/bills/${billId}`);
  }, [view.bill.state, billId, router]);

  const writes = useSplitWrites(billId, ({ error, changes }) => {
    show({
      message: isApiError(error) && error.status !== 0 ? error.title : strings.error.couldntSave,
      variant: "error",
      actionLabel: strings.error.retry,
      // Retry re-sends the final state of the failed cells (set-state, safe to repeat).
      onAction: () => writes.setCells(changes, { immediate: true }),
    });
  });

  const columns = splitColumns(view);
  const assigned = useMemo(() => assignedMap(view.items, writes.overrides), [view.items, writes.overrides]);
  const shares = writes.pending ? previewShares(view, assigned) : view.shares;
  const nameOf = (key: string) => {
    const p = view.participants.find((x) => x.key === key);
    return p ? (meId && p.id === meId ? strings.people.youTag : p.display_name) : "";
  };
  const done = view.items.filter((i) => (assigned[i.id] ?? []).length > 0).length;
  const total = view.items.length;
  const left = total - done;

  const members: SplitMember[] = columns.map((p) => ({
    id: p.key,
    name: displayName(p, meId),
    tag: participantTag(p, meId),
    initial: p.initial,
    colorIndex: colorIndexOf(p),
  }));

  function record(kind: SplitAction["kind"], changes: CellChange[]): void {
    setUndo((stack) => pushUndo(stack, { kind, changes }));
  }

  function toggle(itemId: string, participant: string, on: boolean): void {
    const before = (assigned[itemId] ?? []).includes(participant);
    record("tick", [{ itemId, participant, before, after: on }]);
    writes.setCells([{ itemId, participant, on }]);
  }

  function toggleColumn(participant: string): void {
    const allOn = view.items.every((i) => (assigned[i.id] ?? []).includes(participant));
    const on = !allOn;
    const changes: CellChange[] = view.items.map((i) => ({
      itemId: i.id,
      participant,
      before: (assigned[i.id] ?? []).includes(participant),
      after: on,
    }));
    const action: SplitAction = { kind: "column", changes };
    record("column", changes);
    writes.setCells(
      changes.map((c) => ({ itemId: c.itemId, participant, on })),
      { immediate: true },
    );
    const name = nameOf(participant);
    show({
      message: on ? strings.split.columnOn(name) : strings.split.columnOff(name),
      actionLabel: strings.toast.undo,
      onAction: () => undoAction(action),
    });
  }

  function undoAction(action: SplitAction): void {
    writes.setCells(inverseChanges(action), { immediate: true });
    setUndo((stack) => stack.filter((a) => a !== action));
  }

  function undoLast(): void {
    const { action, stack } = popUndo(undo);
    if (!action) return;
    setUndo(stack);
    writes.setCells(inverseChanges(action), { immediate: true });
  }

  async function fillRest(): Promise<void> {
    if (filling) return;
    setFilling(true);
    const before = assignedMap(view.items, writes.overrides);
    await writes.settle();
    try {
      await api.post<FillRestOut>(`/bills/${billId}/allocations/fill-rest`, undefined, {
        idempotencyKey: newId(),
      });
      await queryClient.invalidateQueries({ queryKey: keys.bill(billId) });
      const fresh = queryClient.getQueryData<BillView>(keys.bill(billId));
      if (fresh) {
        const changes: CellChange[] = [];
        for (const item of fresh.items) {
          const was = before[item.id] ?? [];
          for (const a of item.allocations) {
            if (!was.includes(a.participant)) {
              changes.push({ itemId: item.id, participant: a.participant, before: false, after: true });
            }
          }
        }
        record("fill-rest", changes);
      }
    } catch (error) {
      show({ message: isApiError(error) ? error.title : strings.error.couldntSave, variant: "error" });
    } finally {
      setFilling(false);
    }
  }

  async function addGuest(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const name = guestName.trim();
    if (!name || guestBusy) return;
    setGuestBusy(true);
    setGuestError(null);
    try {
      const out = await api.post<GuestOut>(`/bills/${billId}/guests`, { name }, { idempotencyKey: newId() });
      setGuest(out);
      setGuestName("");
      await queryClient.invalidateQueries({ queryKey: keys.bill(billId) });
    } catch (error) {
      setGuestError(isApiError(error) ? error.title : strings.error.couldntSave);
    } finally {
      setGuestBusy(false);
    }
  }

  const guestUrl = guest && typeof window !== "undefined" ? `${window.location.origin}/g/${guest.link_token}` : "";
  const store = view.bill.store_name ?? strings.bill.untitled;

  return (
    <div className="kt-screen">
      <AppHeader
        stepLabel={strings.split.step(store, shortDate(view.bill.purchase_date))}
        title={strings.split.title}
        back={{ href: `/bills/${billId}/review` }}
      >
        <ProgressBar value={done} max={total} />
        {left > 0 && isPayer ? (
          <PillButton variant="dashed" size={52} fullWidth loading={filling} onClick={() => void fillRest()}>
            {strings.split.fillRest}
          </PillButton>
        ) : null}
        {isPayer ? (
          <div className="kt-split-links">
            <button type="button" className="kt-text-link" onClick={() => setGuestOpen(true)}>
              {strings.split.guest}
            </button>
            {undo.length > 0 ? (
              <button type="button" className="kt-text-link" onClick={undoLast}>
                {strings.split.undoLast}
              </button>
            ) : null}
          </div>
        ) : null}
      </AppHeader>
      <div className="kt-split-screen">
        <SplitGrid
          members={members}
          items={view.items.map((i) => ({
            id: i.id,
            name: i.name,
            priceCents: i.line_total_cents,
            note: itemNote(i, assigned[i.id] ?? [], nameOf, currency),
          }))}
          assigned={assigned}
          pending={new Set([...writes.overrides.keys()].map((k) => k.replace("|", ":")))}
          totals={members.map((m) => ({ memberId: m.id, amountCents: shares[m.id] ?? 0 }))}
          mode={isPayer ? "payer" : "readonly"}
          onToggle={toggle}
          onColumnToggle={isPayer ? toggleColumn : undefined}
          note={strings.split.taxNote(formatMoney(view.bill.tax_cents, currency))}
          currency={currency}
          footer={
            left > 0 ? (
              <div className="kt-split__left">{strings.split.itemsLeft(left)}</div>
            ) : (
              <Link
                href={`/bills/${billId}/send`}
                className="kt-split__done"
                onClick={(event) => {
                  // Let the last ticks land before the summary reads the bill.
                  event.preventDefault();
                  void writes.settle().then(() => router.push(`/bills/${billId}/send`));
                }}
              >
                {strings.split.allSorted}
              </Link>
            )
          }
        />
      </div>
      <BottomSheet
        open={guestOpen}
        onClose={() => {
          setGuestOpen(false);
          setGuest(null);
        }}
        title={strings.split.guestTitle}
      >
        <form className="kt-form" onSubmit={(event) => void addGuest(event)}>
          <div className="kt-field">
            <label htmlFor={guestId} className="kt-field__label">
              {strings.split.guestName}
            </label>
            <input
              id={guestId}
              className="kt-input"
              value={guestName}
              maxLength={24}
              autoComplete="off"
              onChange={(event) => setGuestName(event.target.value)}
            />
            {guestError ? <p className="kt-field-error">{guestError}</p> : null}
          </div>
          <PillButton type="submit" fullWidth loading={guestBusy} disabled={!guestName.trim()}>
            {strings.split.guestAdd}
          </PillButton>
          {guest && guestUrl ? (
            <div className="kt-field">
              <span className="kt-field__label">{strings.split.guestLink}</span>
              <span className="kt-guest-link">{displayLink(guestUrl)}</span>
              <div className="kt-two-up">
                <PillButton
                  size={52}
                  onClick={() =>
                    void shareOrCopy({ title: store, url: guestUrl }).then((r) => {
                      if (r === "copied") show({ message: strings.shareScreen.copied });
                    })
                  }
                >
                  {strings.shareScreen.share}
                </PillButton>
                <PillButton
                  variant="secondary"
                  size={52}
                  onClick={() =>
                    void copyText(guestUrl).then((ok) => ok && show({ message: strings.shareScreen.copied }))
                  }
                >
                  {strings.shareScreen.copyLink}
                </PillButton>
              </div>
            </div>
          ) : null}
        </form>
      </BottomSheet>
      <ScreenToast toast={toast} onDismiss={dismiss} />
    </div>
  );
}
