"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { Dialog } from "@/components/Dialog";
import { ItemRow, type Storage } from "@/components/ItemRow";
import { PillButton } from "@/components/PillButton";
import { ReceiptCard } from "@/components/ReceiptCard";
import { ActionBar } from "@/components/screens/ActionBar";
import { BillGate } from "@/components/screens/BillGate";
import { ItemEditorSheet, type ItemDraft } from "@/components/screens/ItemEditorSheet";
import { ScreenToast } from "@/components/screens/ScreenToast";
import { useToast } from "@/components/screens/useToast";
import { api, isApiError, newId } from "@/lib/api/client";
import { keys } from "@/lib/api/hooks";
import type { BillItem, BillView, HeaderPatch } from "@/lib/api/types";
import {
  centsToInput,
  computedTotal,
  longDate,
  parseCents,
  reconcileDisplay,
} from "@/lib/bills";
import { formatMoney } from "@/lib/format";
import {
  clearScan,
  dismissScan,
  retryScan,
  useScanJob,
  type ScanPhase,
} from "@/lib/receipts/scan-job";
import { strings } from "@/lib/strings";
import { writeThrough } from "@/lib/sync/write-cache";
import { OfferBanner, ReceiptPhoto, ScanFailed, ScanReading } from "./ScanStates";

/** Draft autosave cadence (FR-14). */
export const AUTOSAVE_MS = 2000;

type HeaderForm = {
  store: string;
  date: string;
  payer: string;
  tax: string;
  total: string;
};

function formFrom(view: BillView): HeaderForm {
  return {
    store: view.bill.store_name ?? "",
    date: view.bill.purchase_date,
    payer: view.bill.payer_member_id,
    tax: centsToInput(view.bill.tax_cents),
    total: centsToInput(view.bill.receipt_total_cents),
  };
}

/** The PATCH body for what changed, or null when a field doesn't parse yet. */
export function headerChanges(view: BillView, form: HeaderForm): HeaderPatch | null {
  const out: HeaderPatch = {};
  const store = form.store.trim();
  if (store !== (view.bill.store_name ?? "")) out.store_name = store || null;
  if (form.date && form.date !== view.bill.purchase_date) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(form.date)) return null;
    out.purchase_date = form.date;
  }
  if (form.payer && form.payer !== view.bill.payer_member_id) out.payer_member_id = form.payer;
  const tax = form.tax.trim() === "" ? 0 : parseCents(form.tax);
  if (tax === null) return null;
  if (tax !== view.bill.tax_cents) out.tax_cents = tax;
  const total = form.total.trim() === "" ? null : parseCents(form.total);
  if (form.total.trim() !== "" && total === null) return null;
  if (total !== view.bill.receipt_total_cents) out.receipt_total_cents = total;
  return out;
}

export function ReviewRoute({ id }: { id: string }) {
  return (
    <BillGate id={id} title={strings.review.title}>
      {(view) => <ReviewOrScan view={view} />}
    </BillGate>
  );
}

const SCANNING: readonly ScanPhase[] = ["preparing", "uploading", "reading"];

/**
 * While this device reads the receipt: "READING YOUR RECEIPT…"; when it fails: Try again / Enter by
 * hand (PRD §14A 2, EXTRACT-06). A draft left half-read (reload mid-scan) or failed on the server
 * shows the failure state too, with "Choose the file again".
 */
function ReviewOrScan({ view }: { view: BillView }) {
  const queryClient = useQueryClient();
  const billId = view.bill.id;
  const job = useScanJob(billId);
  const [byHand, setByHand] = useState(false);
  const phase = job?.phase;

  useEffect(() => {
    if (phase !== "done") return;
    void queryClient.invalidateQueries({ queryKey: keys.bill(billId) }).then(() => clearScan(billId));
  }, [phase, billId, queryClient]);

  const enterByHand = () => {
    dismissScan(billId);
    setByHand(true);
  };
  // "done" keeps the reading card up until the refetched bill is in, so the form starts from it.
  if (job && !job.dismissed && (SCANNING.includes(job.phase) || job.phase === "done")) {
    return <ScanReading job={job} onEnterByHand={enterByHand} />;
  }
  if (job && !job.dismissed && job.phase === "failed") {
    return (
      <ScanFailed
        billId={billId}
        message={job.error}
        onRetry={() => retryScan(billId)}
        onEnterByHand={enterByHand}
      />
    );
  }
  const stuck =
    !job &&
    !byHand &&
    view.items.length === 0 &&
    (view.bill.extraction_status === "failed" || view.bill.extraction_status === "pending");
  if (stuck) {
    return (
      <ScanFailed
        billId={billId}
        message={view.bill.extraction_status === "pending" ? strings.scan.interrupted : strings.scan.notReadable}
        onRetry={null}
        onEnterByHand={enterByHand}
      />
    );
  }
  return <ReviewScreen view={view} />;
}

function ReviewScreen({ view }: { view: BillView }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const billId = view.bill.id;
  const { toast, show, dismiss } = useToast();
  const ids = { store: useId(), date: useId(), payer: useId(), tax: useId(), total: useId(), reason: useId() };
  const [form, setForm] = useState<HeaderForm>(() => formFrom(view));
  // Fields typed here and not saved yet. Everything else follows the server (a scan that lands,
  // another device), so a later save never sends stale values back.
  const dirty = useRef<Set<keyof HeaderForm>>(new Set());
  const formRef = useRef(form);
  const viewRef = useRef(view);
  const saving = useRef<Promise<void> | null>(null);
  const timer = useRef<number | null>(null);
  const [editing, setEditing] = useState<BillItem | "new" | null>(null);
  const [itemBusy, setItemBusy] = useState(false);
  const [itemError, setItemError] = useState<string | null>(null);
  const [tagBusy, setTagBusy] = useState<Set<string>>(new Set());
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [overriding, setOverriding] = useState(false);
  const [dupDismissed, setDupDismissed] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const itemsRef = useRef<HTMLUListElement>(null);

  formRef.current = form;
  viewRef.current = view;
  const currency = view.bill.currency;

  useEffect(() => {
    const fresh = formFrom(view);
    setForm((old) => {
      let changed = false;
      const next = { ...old };
      for (const key of Object.keys(fresh) as (keyof HeaderForm)[]) {
        if (!dirty.current.has(key) && old[key] !== fresh[key]) {
          next[key] = fresh[key];
          changed = true;
        }
      }
      return changed ? next : old;
    });
  }, [view]);

  // Only the payer (or the uploader of a draft) edits; everyone else sees the bill page.
  useEffect(() => {
    if (view.bill.state !== "draft" || !view.me.can_edit) router.replace(`/bills/${billId}`);
  }, [view.bill.state, view.me.can_edit, billId, router]);

  const setBill = useCallback(
    (update: (old: BillView) => BillView) =>
      writeThrough<BillView>(queryClient, keys.bill(billId), update),
    [queryClient, billId],
  );
  const refetch = useCallback(() => queryClient.invalidateQueries({ queryKey: keys.bill(billId) }), [queryClient, billId]);

  /** Saves the header now if it changed (guarded edit with If-Match). */
  const saveHeader = useCallback(async (): Promise<void> => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    if (saving.current) await saving.current;
    const current = viewRef.current;
    const sent = formRef.current;
    const body = headerChanges(current, sent);
    if (!body || Object.keys(body).length === 0) return;
    const run = (async () => {
      try {
        const out = await api.patch<{ version: number; total_cents: number }>(
          `/bills/${billId}`,
          body,
          current.bill.version,
        );
        setBill((old) => ({
          ...old,
          bill: {
            ...old.bill,
            version: out.version,
            total_cents: out.total_cents,
            ...(body.store_name !== undefined ? { store_name: body.store_name } : {}),
            ...(body.purchase_date ? { purchase_date: body.purchase_date } : {}),
            ...(body.payer_member_id ? { payer_member_id: body.payer_member_id } : {}),
            ...(body.tax_cents !== undefined && body.tax_cents !== null ? { tax_cents: body.tax_cents } : {}),
            ...(body.receipt_total_cents !== undefined ? { receipt_total_cents: body.receipt_total_cents } : {}),
          },
        }));
        for (const key of [...dirty.current]) if (formRef.current[key] === sent[key]) dirty.current.delete(key);
        await refetch();
      } catch (error) {
        if (isApiError(error) && error.code === "VERSION_CONFLICT") {
          await refetch();
          const fresh = queryClient.getQueryData<BillView>(keys.bill(billId));
          dirty.current.clear();
          if (fresh) setForm(formFrom(fresh));
          show({ message: strings.split.conflict });
        } else {
          show({
            message: isApiError(error) ? error.title : strings.error.couldntSave,
            variant: "error",
            actionLabel: strings.error.retry,
            onAction: () => void saveHeader(),
          });
        }
      }
    })();
    saving.current = run;
    try {
      await run;
    } finally {
      saving.current = null;
    }
  }, [billId, setBill, refetch, queryClient, show]);

  // Draft autosave: at most every 2 s while there are unsaved header changes (FR-14, BILL-06).
  function edit(patch: Partial<HeaderForm>): void {
    for (const key of Object.keys(patch) as (keyof HeaderForm)[]) dirty.current.add(key);
    setForm((old) => ({ ...old, ...patch }));
    if (timer.current === null) {
      timer.current = window.setTimeout(() => {
        timer.current = null;
        void saveHeader();
      }, AUTOSAVE_MS);
    }
  }

  useEffect(
    () => () => {
      if (timer.current !== null) {
        window.clearTimeout(timer.current);
        timer.current = null;
        void saveHeader();
      }
    },
    [saveHeader],
  );

  async function cycleStorage(item: BillItem, next: Storage): Promise<void> {
    if (tagBusy.has(item.id)) return;
    setTagBusy((s) => new Set(s).add(item.id));
    try {
      const updated = await api.patch<BillItem>(`/bills/${billId}/items/${item.id}`, { storage: next }, item.version);
      setBill((old) => ({ ...old, items: old.items.map((i) => (i.id === item.id ? updated : i)) }));
    } catch (error) {
      await refetch();
      show({
        message:
          isApiError(error) && error.code === "VERSION_CONFLICT"
            ? strings.split.conflict
            : isApiError(error)
              ? error.title
              : strings.error.couldntSave,
        variant: "error",
      });
    } finally {
      setTagBusy((s) => {
        const copy = new Set(s);
        copy.delete(item.id);
        return copy;
      });
    }
  }

  async function saveItem(draft: ItemDraft): Promise<void> {
    setItemBusy(true);
    setItemError(null);
    const body = {
      name: draft.name,
      line_total_cents: draft.line_total_cents,
      discount_cents: draft.discount_cents,
      taxable: draft.taxable,
      ...(draft.quantity ? { quantity: draft.quantity } : {}),
      ...(draft.unit_price_cents !== null ? { unit_price_cents: draft.unit_price_cents } : {}),
    };
    try {
      if (editing === "new") {
        const created = await api.post<BillItem>(`/bills/${billId}/items`, body, { idempotencyKey: newId() });
        setBill((old) => ({ ...old, items: [...old.items, created] }));
      } else if (editing) {
        const updated = await api.patch<BillItem>(`/bills/${billId}/items/${editing.id}`, body, editing.version);
        setBill((old) => ({ ...old, items: old.items.map((i) => (i.id === updated.id ? updated : i)) }));
      }
      setEditing(null);
      void refetch();
    } catch (error) {
      if (isApiError(error) && error.code === "VERSION_CONFLICT") {
        void refetch();
        setItemError(strings.split.conflict);
      } else {
        setItemError(isApiError(error) ? error.title : strings.error.couldntSave);
      }
    } finally {
      setItemBusy(false);
    }
  }

  async function deleteItem(): Promise<void> {
    if (!editing || editing === "new") return;
    setItemBusy(true);
    try {
      await api.del(`/bills/${billId}/items/${editing.id}`);
      setBill((old) => ({ ...old, items: old.items.filter((i) => i.id !== editing.id) }));
      setEditing(null);
      void refetch();
    } catch (error) {
      setItemError(isApiError(error) ? error.title : strings.error.couldntSave);
    } finally {
      setItemBusy(false);
    }
  }

  async function override(): Promise<void> {
    if (reason.trim().length < 3) return;
    setOverriding(true);
    try {
      await saveHeader();
      await api.post(`/bills/${billId}/reconcile-override`, { reason: reason.trim() }, { idempotencyKey: newId() });
      await refetch();
      setOverrideOpen(false);
      setReason("");
    } catch (error) {
      show({ message: isApiError(error) ? error.title : strings.error.couldntSave, variant: "error" });
    } finally {
      setOverriding(false);
    }
  }

  async function next(): Promise<void> {
    setLeaving(true);
    await saveHeader();
    const fresh = queryClient.getQueryData<BillView>(keys.bill(billId));
    if (fresh && !fresh.reconciliation.ok) {
      setLeaving(false);
      return;
    }
    router.push(`/bills/${billId}/split`);
  }

  // Local, display-only reconciliation so typing the tax or total updates the line at once.
  const taxCents = parseCents(form.tax || "0") ?? view.bill.tax_cents;
  const totalCents = form.total.trim() === "" ? null : parseCents(form.total);
  const computed = computedTotal(view.items, taxCents, view.fees);
  const overriddenStill =
    view.reconciliation.overridden && totalCents === view.bill.receipt_total_cents;
  const rec = reconcileDisplay(
    { receipt_total_cents: totalCents, overridden: overriddenStill },
    computed,
    currency,
  );
  const subtotal = view.items.reduce((sum, i) => sum + i.line_total_cents, 0);
  const payers = view.participants.filter(
    (p) => p.kind === "member" && (p.status === "active" || p.status === "placeholder"),
  );
  const blockedReason =
    view.items.length === 0 ? strings.review.needsItems : !rec.ok ? strings.review.needsReconcile : undefined;
  const dup = !dupDismissed ? view.duplicate_of : null;
  const offer = view.scanned_offer ?? null;

  return (
    <div className="kt-screen">
      <AppHeader stepLabel={strings.review.step} title={strings.review.title} back={{ href: "/" }} />
      <main className="kt-screen__main">
        {dup ? (
          <div className="kt-banner" role="status">
            <p className="kt-banner__text">
              {strings.review.duplicate(dup.store_name ?? strings.bill.untitled, longDate(dup.purchase_date))}
            </p>
            <div className="kt-inline-actions">
              <Link className="kt-text-link" href={dup.state === "draft" ? `/bills/${dup.id}/review` : `/bills/${dup.id}`}>
                {strings.review.openIt}
              </Link>
              <button type="button" className="kt-text-link" onClick={() => setDupDismissed(true)}>
                {strings.review.differentOrder}
              </button>
            </div>
          </div>
        ) : null}

        {offer ? <OfferBanner view={view} offer={offer} onDone={(message) => show({ message })} /> : null}

        <ReceiptCard heading={strings.review.title} hideHeading>
          <div className="kt-review">
            <ReceiptPhoto billId={billId} />
            <div className="kt-review__store">
              <label htmlFor={ids.store} className="kt-review__caption">
                {strings.review.store}
              </label>
              <input
                id={ids.store}
                className="kt-dotted-input kt-dotted-input--store"
                value={form.store}
                placeholder={strings.review.storePlaceholder}
                maxLength={80}
                autoComplete="off"
                onChange={(event) => edit({ store: event.target.value })}
              />
            </div>
            <div className="kt-review__meta">
              <label htmlFor={ids.date}>
                <span className="kt-review__caption">{strings.review.date}</span>
                <input
                  id={ids.date}
                  type="date"
                  className="kt-dotted-input"
                  value={form.date}
                  onChange={(event) => edit({ date: event.target.value })}
                />
              </label>
              <label htmlFor={ids.payer}>
                <span className="kt-review__caption">{strings.review.paidBy}</span>
                <select
                  id={ids.payer}
                  className="kt-dotted-input"
                  value={form.payer}
                  onChange={(event) => edit({ payer: event.target.value })}
                >
                  {payers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.id === view.me.member_id ? strings.review.you : p.display_name.toUpperCase()}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <hr className="kt-rule" />
            <p className="kt-review__caption">{strings.review.tagsHelp}</p>
            <ul className="kt-review__items" ref={itemsRef}>
              {view.items.map((item) => (
                <li key={item.id} data-busy={tagBusy.has(item.id) || undefined}>
                  <ItemRow
                    name={item.name}
                    priceCents={item.line_total_cents}
                    flagged={item.flagged}
                    storage={item.storage}
                    currency={currency}
                    onStorageChange={(nextStorage) => void cycleStorage(item, nextStorage)}
                    onEdit={() => {
                      setItemError(null);
                      setEditing(item);
                    }}
                  />
                </li>
              ))}
            </ul>
            <button
              type="button"
              className="kt-add-missed"
              onClick={() => {
                setItemError(null);
                setEditing("new");
              }}
            >
              {strings.review.addItem}
            </button>
            <hr className="kt-rule" />
            <dl className="kt-totals">
              <div className="kt-totals__row">
                <dt>{strings.review.subtotal}</dt>
                <dd className="kt-money">{formatMoney(subtotal, currency)}</dd>
              </div>
              <div className="kt-totals__row">
                <dt>
                  <label htmlFor={ids.tax}>{strings.review.tax}</label>
                </dt>
                <dd>
                  <input
                    id={ids.tax}
                    className="kt-totals__input kt-money"
                    inputMode="decimal"
                    value={form.tax}
                    placeholder="0.00"
                    onChange={(event) => edit({ tax: event.target.value })}
                  />
                </dd>
              </div>
              {view.fees.map((fee) => (
                <div className="kt-totals__row" key={fee.id}>
                  <dt>{fee.label.toUpperCase()}</dt>
                  <dd className="kt-money">
                    {formatMoney(fee.kind === "order_discount" ? -fee.amount_cents : fee.amount_cents, currency)}
                  </dd>
                </div>
              ))}
              <div className="kt-totals__row" data-total="">
                <dt>
                  <label htmlFor={ids.total}>{strings.review.total}</label>
                </dt>
                <dd>
                  <input
                    id={ids.total}
                    className="kt-totals__input kt-money"
                    inputMode="decimal"
                    value={form.total}
                    placeholder={centsToInput(computed)}
                    aria-invalid={!rec.ok || undefined}
                    onChange={(event) => edit({ total: event.target.value })}
                  />
                </dd>
              </div>
            </dl>
            <p className="kt-reconcile" data-ok={rec.ok} role="status">
              {rec.text}
            </p>
            {!rec.ok ? (
              <div className="kt-reconcile-actions">
                <PillButton
                  variant="secondary"
                  size={44}
                  onClick={() => itemsRef.current?.querySelector<HTMLButtonElement>("button")?.focus()}
                >
                  {strings.review.fixItems}
                </PillButton>
                <PillButton variant="dashed" size={44} onClick={() => setOverrideOpen(true)}>
                  {strings.review.useReceiptTotal}
                </PillButton>
              </div>
            ) : null}
          </div>
        </ReceiptCard>
      </main>
      <ActionBar>
        <PillButton
          fullWidth
          disabled={Boolean(blockedReason)}
          disabledReason={blockedReason}
          loading={leaving}
          onClick={() => void next()}
        >
          {strings.review.next}
        </PillButton>
      </ActionBar>

      <ItemEditorSheet
        open={editing !== null}
        item={editing === "new" ? null : editing}
        onClose={() => setEditing(null)}
        onSave={saveItem}
        onDelete={deleteItem}
        busy={itemBusy}
        error={itemError}
      />
      <Dialog
        open={overrideOpen}
        onClose={() => setOverrideOpen(false)}
        title={strings.review.useReceiptTotal}
        confirmLabel={strings.review.useReceiptTotal}
        confirmDisabled={reason.trim().length < 3}
        confirmLoading={overriding}
        onConfirm={() => void override()}
      >
        <div className="kt-field">
          <label htmlFor={ids.reason} className="kt-field__label">
            {strings.review.reasonLabel}
          </label>
          <textarea
            id={ids.reason}
            className="kt-input kt-textarea"
            value={reason}
            maxLength={200}
            onChange={(event) => setReason(event.target.value)}
          />
        </div>
      </Dialog>
      <ScreenToast toast={toast} onDismiss={dismiss} />
    </div>
  );
}
