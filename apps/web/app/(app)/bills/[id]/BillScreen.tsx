"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityTimeline } from "@/components/ActivityTimeline";
import { AppHeader } from "@/components/AppHeader";
import { PillButton } from "@/components/PillButton";
import { ReceiptCard } from "@/components/ReceiptCard";
import { SegmentedControl } from "@/components/SegmentedControl";
import { Skeleton } from "@/components/Skeleton";
import { SplitGrid, type SplitMember } from "@/components/SplitGrid";
import { StampChip } from "@/components/StampChip";
import { WhoOwesRow } from "@/components/WhoOwesRow";
import { BellIcon, XIcon } from "@/components/icons";
import { ActionBar } from "@/components/screens/ActionBar";
import { BillGate } from "@/components/screens/BillGate";
import { ItemEditorSheet, type ItemDraft } from "@/components/screens/ItemEditorSheet";
import { ScreenToast } from "@/components/screens/ScreenToast";
import { useToast } from "@/components/screens/useToast";
import {
  billLifeKeys,
  useBillActivity,
  useBillMoney,
  type RecordedPayment,
} from "@/lib/api/bill-life";
import { api, isApiError, newId } from "@/lib/api/client";
import { keys } from "@/lib/api/hooks";
import type { BillItem, BillView, GuestOut } from "@/lib/api/types";
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
import {
  activityLine,
  agoText,
  canReopen,
  dayOpen,
  owesRows,
  problemText,
  remindedRecently,
  signedMoney,
  type OwesRowModel,
} from "@/lib/open-bill";
import { useSplitWrites } from "@/lib/split/useSplitWrites";
import { strings } from "@/lib/strings";
import { serverNow } from "@/lib/server-time";
import { useServerNow } from "@/lib/use-server-time";
import { writeThrough } from "@/lib/sync/write-cache";
import { lastActorOf } from "@/lib/sync/actors";
import { useOnline } from "@/lib/sync/online";
import { emitRevoked } from "@/lib/sync/revoked";
import {
  AddGuestSheet,
  CashSheet,
  ConflictDialog,
  MenuSheet,
  PaySheet,
  RejectDialog,
  SettleDialog,
  VoidDialog,
  type MenuAction,
} from "./BillSheets";

const S = strings.openBill;
const OFFLINE = strings.button.needsConnection;

export function BillRoute({ id }: { id: string }) {
  return (
    <BillGate id={id} title={strings.bill.untitled}>
      {(view) => (view.bill.state === "draft" ? <DraftBill view={view} /> : <OpenBill view={view} />)}
    </BillGate>
  );
}

function headerSubtitle(view: BillView): string {
  const payer = view.participants.find((p) => p.id === view.bill.payer_member_id);
  return strings.bill.subtitle(
    shortDate(view.bill.purchase_date),
    payer ? displayName(payer, view.me.member_id) : "",
    formatMoney(view.bill.total_cents, view.bill.currency),
  );
}

/** A draft has no screen 5 yet: the way back into the split, and the shares so far. */
function DraftBill({ view }: { view: BillView }) {
  const store = view.bill.store_name ?? strings.bill.untitled;
  return (
    <div className="kt-screen">
      <AppHeader title={store} subtitle={headerSubtitle(view)} back={{ href: "/" }} />
      <main className="kt-screen__main">
        {view.me.can_edit ? (
          <PillButton href={`/bills/${view.bill.id}/review`} variant="dashed" fullWidth>
            {strings.bill.draft(store)}
          </PillButton>
        ) : null}
      </main>
    </div>
  );
}

type Busy =
  | null
  | "pay"
  | "cash"
  | "settle"
  | "reopen"
  | "void"
  | "reject"
  | "item"
  | `confirm:${string}`
  | `remind:${string}`;

/**
 * Screen 5, the open bill (PRD §14A 5, Bill.dc.html): who owes what with payment progress,
 * payments (record, confirm, reject, cash), reminders, settle / reopen / void, the story so far,
 * the change badge, and the split grid where members tick their own column. Live through the
 * house stream (lib/sync); only ticks are optimistic, every other write waits for the server.
 */
function OpenBill({ view }: { view: BillView }) {
  const queryClient = useQueryClient();
  const online = useOnline();
  const billId = view.bill.id;
  const houseId = view.bill.house_id;
  const currency = view.bill.currency;
  const state = view.bill.state;
  const isOpen = state === "open";
  const meId = view.me.member_id;
  const meKey = meId ? `m:${meId}` : null;
  const isPayer = view.me.is_payer;
  const store = view.bill.store_name ?? strings.bill.untitled;
  const payer = view.participants.find((p) => p.id === view.bill.payer_member_id);
  const payerName = payer?.display_name ?? "";
  const meName = view.participants.find((p) => p.id === meId)?.display_name ?? null;
  const now = useServerNow(60_000);

  const money = useBillMoney(billId);
  const activity = useBillActivity(billId);
  const { toast, show, dismiss } = useToast();
  const [tab, setTab] = useState<"owes" | "items">("owes");
  const [busy, setBusy] = useState<Busy>(null);
  const [sheet, setSheet] = useState<null | "pay" | "cash" | "menu" | "guest">(null);
  const [dialog, setDialog] = useState<null | "settle" | "void">(null);
  const [rejecting, setRejecting] = useState<OwesRowModel | null>(null);
  const [voidError, setVoidError] = useState<string | null>(null);
  const [nudged, setNudged] = useState<ReadonlySet<string>>(new Set());
  const [stamped, setStamped] = useState(false);
  // EDGE-15-25: a gentle nudge to the payer after 60 days; it never settles on its own.
  const [longOpenGone, setLongOpenGone] = useState(false);
  const payKey = useRef<string>(newId());

  const refreshLife = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: billLifeKeys.money(billId) });
    void queryClient.invalidateQueries({ queryKey: billLifeKeys.activity(billId) });
  }, [queryClient, billId]);
  const refreshAll = useCallback(
    () => queryClient.invalidateQueries({ queryKey: keys.bill(billId) }),
    [queryClient, billId],
  );

  /** One place for failed writes: revoked → leave; state conflicts → refetch and say why. */
  const failed = useCallback(
    (error: unknown, retry?: () => void) => {
      if (isApiError(error) && error.code === "NOT_A_MEMBER") {
        emitRevoked(houseId);
        return;
      }
      if (isApiError(error) && ["BILL_SETTLED", "BILL_VOIDED", "ITEM_GONE", "CONFLICT", "ALREADY_ANSWERED"].includes(error.code)) {
        void refreshAll();
      }
      // No answer, or the server failed: the same write may well succeed, so offer Retry (PRD §15
      // "Write fails or times out", EDGE-15-26). Offline writes are refused, not retried.
      const transient =
        !isApiError(error) || (error.code !== "OFFLINE" && (error.status === 0 || error.status >= 500));
      show({
        message: transient && retry ? strings.sync.couldntSaveRetry : problemText(error),
        variant: "error",
        ...(transient && retry ? { actionLabel: strings.error.retry, onAction: retry } : {}),
      });
    },
    [houseId, refreshAll, show],
  );

  // ---- change badge (FR-35): shown from the first load, cleared on the server by PUT seen ----
  const [firstChange, setFirstChange] = useState<{ delta_cents: number } | null | undefined>(undefined);
  const [bannerGone, setBannerGone] = useState<string | null>(null);
  const seenSent = useRef(false);
  useEffect(() => {
    if (!money.data || seenSent.current) return;
    seenSent.current = true;
    setFirstChange(money.data.changed);
    void api.put(`/bills/${billId}/seen`, { version: money.data.version }).catch(() => undefined);
  }, [money.data, billId]);
  const liveChange = money.data?.changed ?? null;
  const change = liveChange ?? firstChange ?? null;
  const changeKey = change ? `${change.delta_cents}` : null;
  const showChange = change !== null && changeKey !== bannerGone;
  function dismissChange(): void {
    setBannerGone(changeKey);
    if (money.data) void api.put(`/bills/${billId}/seen`, { version: money.data.version }).catch(() => undefined);
  }

  // ---- who owes ----
  const rows = useMemo(() => owesRows(view, money.data), [view, money.data]);
  const myRow = rows.find((r) => r.isMe && !r.isPayer) ?? null;
  const nameOf = (row: OwesRowModel) => displayName(row.participant, meId);
  const entries = useMemo(() => activity.data?.pages.flatMap((p) => p.events) ?? [], [activity.data]);
  const lines = useMemo(
    () => entries.map((e) => activityLine(e, { view, meName })),
    [entries, view, meName],
  );

  async function remind(row: OwesRowModel): Promise<void> {
    if (busy) return;
    setBusy(`remind:${row.key}`);
    try {
      await api.post(`/bills/${billId}/remind`, { member_id: row.participant.id }, { idempotencyKey: newId() });
      setNudged((old) => new Set(old).add(row.participant.id));
      show({ message: S.reminded(nameOf(row)) });
      refreshLife();
    } catch (error) {
      if (isApiError(error) && error.status === 429) setNudged((old) => new Set(old).add(row.participant.id));
      failed(error);
    } finally {
      setBusy(null);
    }
  }

  async function answer(row: OwesRowModel, confirm: boolean, note: string | null = null): Promise<void> {
    const payment = row.pending;
    if (!payment || busy) return;
    setBusy(confirm ? `confirm:${payment.id}` : "reject");
    try {
      await api.post(
        `/payments/${payment.id}/${confirm ? "confirm" : "reject"}`,
        confirm ? undefined : { note },
        { idempotencyKey: newId() },
      );
      show({
        message: confirm
          ? S.confirmed(nameOf(row), formatMoney(payment.amount_cents, currency))
          : S.rejected(nameOf(row)),
      });
      setRejecting(null);
      refreshLife();
    } catch (error) {
      setRejecting(null);
      failed(error);
      refreshLife();
    } finally {
      setBusy(null);
    }
  }

  async function markPaid(amountCents: number, method: string): Promise<void> {
    if (busy) return;
    setBusy("pay");
    try {
      // One key per "I've paid" action: a double tap or a lost response returns the first (SYNC-09).
      const out = await api.post<RecordedPayment>(
        `/bills/${billId}/payments`,
        { amount_cents: amountCents, method },
        { idempotencyKey: payKey.current },
      );
      payKey.current = newId();
      setSheet(null);
      show({
        message: out.warning
          ? S.duplicate(formatMoney(out.warning.amount_cents, currency), agoText(out.warning.recorded_at, serverNow()))
          : S.marked(payerName),
      });
      refreshLife();
    } catch (error) {
      if (isApiError(error) && error.status !== 0) {
        payKey.current = newId();
        setSheet(null);
      }
      failed(error, () => void markPaid(amountCents, method));
    } finally {
      setBusy(null);
    }
  }

  async function recordCash(participant: string, amountCents: number): Promise<void> {
    if (busy) return;
    setBusy("cash");
    const row = rows.find((r) => r.key === participant);
    try {
      await api.post(
        `/bills/${billId}/payments`,
        { participant, amount_cents: amountCents, method: "cash" },
        { idempotencyKey: newId() },
      );
      setSheet(null);
      show({ message: S.cashDone(row ? nameOf(row) : "", formatMoney(amountCents, currency)) });
      refreshLife();
    } catch (error) {
      setSheet(null);
      failed(error);
    } finally {
      setBusy(null);
    }
  }

  const outstanding = rows
    .filter((r) => !r.isPayer && r.owedCents !== 0)
    .map((r) => ({ key: r.key, name: nameOf(r), owedCents: r.owedCents }));

  async function settle(): Promise<void> {
    if (busy) return;
    setBusy("settle");
    try {
      await api.post(
        `/bills/${billId}/settle`,
        { confirm_outstanding: outstanding.length > 0 },
        { idempotencyKey: newId() },
      );
      setDialog(null);
      setStamped(true);
      await refreshAll();
    } catch (error) {
      // Someone paid or ticked while the dialog was open: show the new list and ask again.
      if (isApiError(error) && error.code === "CONFLICT" && Array.isArray(error.body.outstanding)) {
        refreshLife();
      } else {
        setDialog(null);
        failed(error);
      }
    } finally {
      setBusy(null);
    }
  }

  async function reopen(): Promise<void> {
    if (busy) return;
    setBusy("reopen");
    try {
      await api.post(`/bills/${billId}/reopen`, undefined, { idempotencyKey: newId() });
      setStamped(false);
      show({ message: S.reopenDone });
      await refreshAll();
    } catch (error) {
      failed(error);
    } finally {
      setBusy(null);
    }
  }

  async function voidBill(reason: string): Promise<void> {
    if (busy) return;
    setBusy("void");
    setVoidError(null);
    try {
      await api.post(`/bills/${billId}/void`, { reason }, { idempotencyKey: newId() });
      setDialog(null);
      await refreshAll();
    } catch (error) {
      if (isApiError(error) && error.status === 422) setVoidError(error.title);
      else {
        setDialog(null);
        failed(error);
      }
    } finally {
      setBusy(null);
    }
  }

  async function addGuest(name: string): Promise<{ link: string } | { error: string }> {
    try {
      const out = await api.post<GuestOut>(`/bills/${billId}/guests`, { name }, { idempotencyKey: newId() });
      void refreshAll();
      return { link: `${window.location.origin}/g/${out.link_token}` };
    } catch (error) {
      if (isApiError(error) && error.code === "NOT_A_MEMBER") emitRevoked(houseId);
      return { error: problemText(error) };
    }
  }

  // ---- items & splits: members tick their own column; the payer all columns and item edits ----
  const writes = useSplitWrites(billId, ({ error, changes }) => {
    failed(error, () => writes.setCells(changes, { immediate: true }));
  });
  const columns = splitColumns(view);
  const assigned = useMemo(() => assignedMap(view.items, writes.overrides), [view.items, writes.overrides]);
  const gridShares = writes.pending ? previewShares(view, assigned) : view.shares;
  const nameOfKey = (key: string) => {
    const p = view.participants.find((x) => x.key === key);
    return p ? (meId && p.id === meId ? strings.people.youTag : p.display_name) : "";
  };
  const members: SplitMember[] = columns.map((p) => ({
    id: p.key,
    name: displayName(p, meId),
    tag: participantTag(p, meId),
    initial: p.initial,
    colorIndex: colorIndexOf(p),
  }));
  const gridMode = !isOpen || !online ? "readonly" : isPayer ? "payer" : meKey ? "member" : "readonly";

  const [editing, setEditing] = useState<BillItem | null>(null);
  const [itemError, setItemError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ item: BillItem; theirs: BillItem; mine: ItemDraft } | null>(null);

  function itemBody(draft: ItemDraft) {
    return {
      name: draft.name,
      line_total_cents: draft.line_total_cents,
      discount_cents: draft.discount_cents,
      taxable: draft.taxable,
      ...(draft.quantity ? { quantity: draft.quantity } : {}),
      ...(draft.unit_price_cents !== null ? { unit_price_cents: draft.unit_price_cents } : {}),
    };
  }

  async function saveItem(draft: ItemDraft, item: BillItem, version: number): Promise<boolean> {
    setBusy("item");
    setItemError(null);
    try {
      const updated = await api.patch<BillItem>(`/bills/${billId}/items/${item.id}`, itemBody(draft), version);
      writeThrough<BillView>(queryClient, keys.bill(billId), (old) => ({
        ...old,
        items: old.items.map((i) => (i.id === updated.id ? updated : i)),
      }));
      void refreshAll();
      return true;
    } catch (error) {
      if (isApiError(error) && error.code === "VERSION_CONFLICT" && error.body.current) {
        // Guarded edit lost the race (SYNC-05): show theirs vs yours and let the payer choose.
        setEditing(null);
        setConflict({ item, theirs: error.body.current as BillItem, mine: draft });
        void refreshAll();
      } else if (isApiError(error) && error.code === "NOT_A_MEMBER") {
        emitRevoked(houseId);
      } else {
        setItemError(problemText(error));
        if (isApiError(error) && error.status !== 0) void refreshAll();
      }
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function deleteItem(item: BillItem): Promise<void> {
    setBusy("item");
    try {
      await api.del(`/bills/${billId}/items/${item.id}`);
      setEditing(null);
      void refreshAll();
    } catch (error) {
      setEditing(null);
      failed(error);
    } finally {
      setBusy(null);
    }
  }

  const conflictWho = (() => {
    if (!conflict) return strings.sync.someone;
    const actor = lastActorOf(conflict.item.id);
    const p = actor ? view.participants.find((x) => x.id === actor) : undefined;
    return p ? displayName(p, meId) : strings.sync.someone;
  })();

  // ---- header menu ----
  const menu: MenuAction[] = [];
  if (isPayer && isOpen) {
    menu.push({ key: "guest", label: S.menuAddGuest, write: true, onSelect: () => setSheet("guest") });
    menu.push({ key: "cash", label: S.menuRecordCash, write: true, onSelect: () => setSheet("cash") });
  }
  menu.push({ key: "csv", label: S.menuExport, href: `/api/v1/bills/${billId}/export.csv` });
  if (isPayer && isOpen) {
    menu.push({
      key: "void",
      label: S.menuVoid,
      danger: true,
      write: true,
      onSelect: () => {
        setSheet(null);
        setVoidError(null);
        setDialog("void");
      },
    });
  }

  const settledDate = view.bill.settled_at ? shortDate(view.bill.settled_at.slice(0, 10)) : "";
  const overlay =
    state === "settled" ? (
      <StampChip large rotate={-4} animate={stamped}>
        {strings.stamp.settled(settledDate)}
      </StampChip>
    ) : state === "voided" ? (
      <StampChip large tone="muted" rotate={-3}>
        {strings.stamp.voided}
      </StampChip>
    ) : undefined;

  const offlineReason = online ? undefined : OFFLINE;
  const footer = (() => {
    if (isOpen && isPayer) {
      return (
        <div className="kt-bill__actions">
          <PillButton variant="secondary" size={52} onClick={() => setTab("items")}>
            {S.editSplits}
          </PillButton>
          <PillButton
            size={56}
            disabled={!online}
            disabledReason={offlineReason}
            onClick={() => setDialog("settle")}
          >
            {S.stampIt}
          </PillButton>
        </div>
      );
    }
    if (isOpen && myRow) {
      const primary = myRow.pending ? (
        <PillButton size={56} disabled disabledReason={undefined}>
          {S.paidWaiting(payerName)}
        </PillButton>
      ) : myRow.owedCents > 0 ? (
        <PillButton size={56} disabled={!online} disabledReason={offlineReason} onClick={() => setSheet("pay")}>
          {S.pay(formatMoney(myRow.owedCents, currency))}
        </PillButton>
      ) : null;
      return (
        <div className="kt-bill__actions">
          <PillButton variant="secondary" size={52} onClick={() => setTab("items")}>
            {S.editMine}
          </PillButton>
          {primary}
        </div>
      );
    }
    if (state === "settled" && isPayer && canReopen(view.bill.settled_at, now)) {
      return (
        <PillButton
          fullWidth
          disabled={!online}
          disabledReason={offlineReason}
          loading={busy === "reopen"}
          onClick={() => void reopen()}
        >
          {S.reopen}
        </PillButton>
      );
    }
    return null;
  })();

  return (
    <div className="kt-screen kt-bill" data-state={state}>
      <AppHeader
        title={store}
        subtitle={headerSubtitle(view)}
        back={{ href: "/" }}
        onMore={() => setSheet("menu")}
      />
      <main className="kt-screen__main">
        {showChange && change ? (
          <div className="kt-changed" role="status">
            <p className="kt-changed__text">{S.changed(signedMoney(change.delta_cents, currency))}</p>
            <button type="button" className="kt-icon-button kt-changed__close" aria-label={S.dismiss} onClick={dismissChange}>
              <XIcon size={18} />
            </button>
          </div>
        ) : null}
        {view.long_open && isPayer && isOpen && !longOpenGone ? (
          <div className="kt-changed" data-tone="gentle" role="status">
            <p className="kt-changed__text">{S.longOpen(dayOpen(view.bill.sent_at, now))}</p>
            <button
              type="button"
              className="kt-icon-button kt-changed__close"
              aria-label={S.dismiss}
              onClick={() => setLongOpenGone(true)}
            >
              <XIcon size={18} />
            </button>
          </div>
        ) : null}

        <SegmentedControl
          label={S.views}
          value={tab}
          onChange={setTab}
          options={[
            { value: "owes", label: S.whoOwes },
            { value: "items", label: S.itemsSplits },
          ]}
        />

        {tab === "owes" ? (
          <>
            <ReceiptCard
              heading={strings.owes.heading}
              stamp={isOpen ? <StampChip rotate={3}>{strings.owes.stillOpen(dayOpen(view.bill.sent_at, now))}</StampChip> : undefined}
              overlay={overlay}
              muted={state === "voided"}
            >
              {money.isPending ? <Skeleton lines={4} /> : null}
              <ul className="kt-owes-list" aria-busy={money.isFetching || undefined}>
                {rows.map((row) => {
                  const name = nameOf(row);
                  const canNudge =
                    isPayer &&
                    isOpen &&
                    row.status === "unpaid" &&
                    row.participant.kind === "member" &&
                    !row.isMe;
                  const nudgedAlready =
                    nudged.has(row.participant.id) || remindedRecently(entries, row.participant.id, now);
                  let action = null;
                  if (canNudge) {
                    action = (
                      <button
                        type="button"
                        className="kt-round-button"
                        aria-label={strings.owes.nudge(name)}
                        title={!online ? OFFLINE : nudgedAlready ? S.reminded(name) : undefined}
                        disabled={!online || nudgedAlready || busy === `remind:${row.key}`}
                        onClick={() => void remind(row)}
                      >
                        <BellIcon size={18} />
                      </button>
                    );
                  } else if (row.isMe && !isPayer && isOpen && row.status === "unpaid") {
                    action = (
                      <PillButton
                        size={44}
                        disabled={!online}
                        aria-label={online ? undefined : `${strings.owes.pay} · ${OFFLINE}`}
                        onClick={() => setSheet("pay")}
                      >
                        {strings.owes.pay}
                      </PillButton>
                    );
                  }
                  const below =
                    isPayer && isOpen && row.pending ? (
                      <>
                        <PillButton
                          size={44}
                          disabled={!online}
                          disabledReason={offlineReason}
                          loading={busy === `confirm:${row.pending.id}`}
                          onClick={() => void answer(row, true)}
                        >
                          {strings.owes.confirm}
                        </PillButton>
                        <PillButton
                          variant="secondary"
                          size={44}
                          disabled={!online}
                          onClick={() => setRejecting(row)}
                        >
                          {strings.owes.reject}
                        </PillButton>
                      </>
                    ) : null;
                  return (
                    <WhoOwesRow
                      key={row.key}
                      name={name}
                      initial={row.participant.initial}
                      colorIndex={colorIndexOf(row.participant)}
                      amountCents={row.shareCents}
                      status={row.status}
                      statusAmountCents={row.statusAmountCents}
                      statusText={
                        row.status === "credit"
                          ? S.paidBackTo(
                              formatMoney(row.confirmedCents, currency),
                              formatMoney(-row.owedCents, currency),
                              name.toUpperCase(),
                            )
                          : undefined
                      }
                      action={action}
                      below={below}
                      currency={currency}
                    />
                  );
                })}
              </ul>
              {state === "voided" ? (
                <p className="kt-bill__void">
                  <s className="kt-money">{formatMoney(view.bill.total_cents, currency)}</s>{" "}
                  {S.voidedReason(view.bill.void_reason ?? "")}
                </p>
              ) : null}
            </ReceiptCard>

            <section className="kt-bill__story" aria-labelledby={`story-${billId}`}>
              <h2 id={`story-${billId}`} className="kt-bill__story-heading">
                {strings.activity.heading}
              </h2>
              {activity.isPending ? <Skeleton lines={3} /> : null}
              <ActivityTimeline events={lines} limit={Math.max(lines.length, 1)} />
              {activity.hasNextPage ? (
                <button
                  type="button"
                  className="kt-text-button"
                  disabled={activity.isFetchingNextPage}
                  onClick={() => void activity.fetchNextPage()}
                >
                  {S.showMore}
                </button>
              ) : null}
            </section>
          </>
        ) : (
          <div className="kt-bill__grid">
            <SplitGrid
              members={members}
              items={view.items.map((i) => ({
                id: i.id,
                name: i.name,
                priceCents: i.line_total_cents,
                note: itemNote(i, assigned[i.id] ?? [], nameOfKey, currency),
              }))}
              assigned={assigned}
              pending={new Set([...writes.overrides.keys()].map((k) => k.replace("|", ":")))}
              totals={members.map((m) => ({ memberId: m.id, amountCents: gridShares[m.id] ?? 0 }))}
              mode={gridMode}
              currentMemberId={meKey ?? undefined}
              onToggle={(itemId, participant, on) => writes.setCells([{ itemId, participant, on }])}
              onItemEdit={
                isPayer && isOpen && online
                  ? (itemId) => {
                      setItemError(null);
                      setEditing(view.items.find((i) => i.id === itemId) ?? null);
                    }
                  : undefined
              }
              note={strings.split.taxNote(formatMoney(view.bill.tax_cents, currency))}
              currency={currency}
            />
          </div>
        )}
      </main>

      {footer ? <ActionBar>{footer}</ActionBar> : null}

      <PaySheet
        open={sheet === "pay"}
        onClose={() => setSheet(null)}
        payerName={payerName}
        owedCents={Math.max(0, myRow?.owedCents ?? 0)}
        handles={money.data?.payer_handles}
        note={`${store} ${shortDate(view.bill.purchase_date)}`}
        currency={currency}
        online={online}
        busy={busy === "pay"}
        onMark={(amount, method) => void markPaid(amount, method)}
      />
      <CashSheet
        open={sheet === "cash"}
        onClose={() => setSheet(null)}
        rows={rows.filter((r) => !r.isPayer && r.owedCents > 0)}
        nameOf={nameOf}
        currency={currency}
        online={online}
        busy={busy === "cash"}
        onRecord={(participant, amount) => void recordCash(participant, amount)}
      />
      <MenuSheet open={sheet === "menu"} onClose={() => setSheet(null)} actions={menu} online={online} />
      <AddGuestSheet open={sheet === "guest"} onClose={() => setSheet(null)} online={online} onAdd={addGuest} />
      <SettleDialog
        open={dialog === "settle"}
        onClose={() => setDialog(null)}
        outstanding={outstanding}
        payerName={strings.people.you}
        currency={currency}
        busy={busy === "settle"}
        onConfirm={() => void settle()}
      />
      <VoidDialog
        open={dialog === "void"}
        onClose={() => setDialog(null)}
        busy={busy === "void"}
        serverError={voidError}
        onConfirm={(reason) => void voidBill(reason)}
      />
      <RejectDialog
        open={rejecting !== null}
        title={
          rejecting?.pending
            ? S.rejectTitle(nameOf(rejecting), formatMoney(rejecting.pending.amount_cents, currency))
            : ""
        }
        onClose={() => setRejecting(null)}
        busy={busy === "reject"}
        onConfirm={(note) => rejecting && void answer(rejecting, false, note)}
      />
      <ItemEditorSheet
        open={editing !== null}
        item={editing}
        onClose={() => setEditing(null)}
        busy={busy === "item"}
        error={itemError}
        onSave={async (draft) => {
          if (!editing) return;
          if (await saveItem(draft, editing, editing.version)) setEditing(null);
        }}
        onDelete={editing ? () => deleteItem(editing) : undefined}
      />
      <ConflictDialog
        open={conflict !== null}
        who={conflictWho}
        theirs={conflict?.theirs ?? null}
        mine={conflict ? { name: conflict.mine.name, line_total_cents: conflict.mine.line_total_cents } : null}
        currency={currency}
        busy={busy === "item"}
        onUseTheirs={() => setConflict(null)}
        onKeepMine={() => {
          if (!conflict) return;
          const { item, theirs, mine } = conflict;
          setConflict(null);
          void saveItem(mine, item, theirs.version);
        }}
      />
      <ScreenToast toast={toast} onDismiss={dismiss} />
    </div>
  );
}
