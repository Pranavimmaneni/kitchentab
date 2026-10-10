"use client";

import { useEffect, useId, useState, type FormEvent } from "react";
import { BottomSheet } from "@/components/BottomSheet";
import { Dialog } from "@/components/Dialog";
import { PillButton } from "@/components/PillButton";
import type { PaymentHandles } from "@/lib/api/bill-life";
import type { BillItem } from "@/lib/api/types";
import { centsToInput, parseCents } from "@/lib/bills";
import { formatMoney } from "@/lib/format";
import type { OwesRowModel } from "@/lib/open-bill";
import { payLinks } from "@/lib/open-bill";
import { copyText, displayLink } from "@/lib/share";
import { strings } from "@/lib/strings";

const S = strings.openBill;
const OFFLINE = strings.button.needsConnection;

/** Amount field shared by the pay and cash sheets: integer cents, never floats. */
function AmountField({
  value,
  onChange,
  error,
}: {
  value: string;
  onChange: (value: string) => void;
  error: boolean;
}) {
  const id = useId();
  const errorId = useId();
  return (
    <div className="kt-field">
      <label htmlFor={id} className="kt-field__label">
        {S.amount}
      </label>
      <input
        id={id}
        className="kt-input kt-input--big kt-input--mono"
        inputMode="decimal"
        autoComplete="off"
        value={value}
        aria-invalid={error || undefined}
        aria-describedby={error ? errorId : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {error ? (
        <p id={errorId} className="kt-field-error">
          {S.amountError}
        </p>
      ) : null}
    </div>
  );
}

export type PaySheetProps = {
  open: boolean;
  onClose: () => void;
  payerName: string;
  owedCents: number;
  handles: PaymentHandles | undefined;
  note: string;
  currency: string;
  online: boolean;
  busy: boolean;
  onMark: (amountCents: number, method: string) => void;
};

/** Pay sheet (PRD §14A, screens with no mockup): deep links, editable amount, "I've paid — mark it". */
export function PaySheet({
  open,
  onClose,
  payerName,
  owedCents,
  handles,
  note,
  currency,
  online,
  busy,
  onMark,
}: PaySheetProps) {
  const [amount, setAmount] = useState(centsToInput(owedCents));
  const [error, setError] = useState(false);
  const [method, setMethod] = useState("other");
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!open) return;
    setAmount(centsToInput(owedCents));
    setError(false);
    setMethod("other");
    setCopied(false);
  }, [open, owedCents]);
  const cents = parseCents(amount);
  const links = payLinks(handles, cents ?? owedCents, note);
  const hasAny = links.length > 0 || Boolean(handles?.zelle);

  function submit(event?: FormEvent<HTMLFormElement>): void {
    event?.preventDefault();
    if (cents === null || cents <= 0) {
      setError(true);
      return;
    }
    onMark(cents, method);
  }

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      title={S.paySheetTitle(payerName, formatMoney(owedCents, currency))}
      footer={
        <PillButton
          fullWidth
          loading={busy}
          disabled={!online}
          disabledReason={online ? undefined : OFFLINE}
          onClick={() => submit()}
        >
          {S.markPaid}
        </PillButton>
      }
    >
      <form className="kt-form" onSubmit={submit} noValidate>
        <AmountField
          value={amount}
          error={error}
          onChange={(value) => {
            setAmount(value);
            setError(false);
          }}
        />
        {links.map((link) => (
          <a
            key={link.method}
            className="kt-pill"
            data-variant="secondary"
            data-size={52}
            data-full=""
            href={link.href}
            onClick={() => {
              setMethod(link.method);
              if (link.fallback) {
                const fallback = link.fallback;
                // No Venmo app: the page is still visible after a moment, so open the web page.
                window.setTimeout(() => {
                  if (document.visibilityState === "visible") window.open(fallback, "_blank", "noopener");
                }, 1500);
              }
            }}
          >
            <span>{S.payWith(link.label)}</span>
          </a>
        ))}
        {handles?.zelle ? (
          <div className="kt-pay-zelle">
            <span className="kt-pay-zelle__handle">{S.zelle(handles.zelle)}</span>
            <button
              type="button"
              className="kt-copy-button"
              onClick={() => {
                setMethod("zelle");
                void copyText(handles.zelle!).then(setCopied);
              }}
            >
              {copied ? S.copied : S.copy}
            </button>
          </div>
        ) : null}
        {!hasAny ? <p className="kt-helper">{S.noHandles(payerName)}</p> : null}
      </form>
    </BottomSheet>
  );
}

export type CashSheetProps = {
  open: boolean;
  onClose: () => void;
  rows: readonly OwesRowModel[];
  nameOf: (row: OwesRowModel) => string;
  currency: string;
  online: boolean;
  busy: boolean;
  onRecord: (participant: string, amountCents: number) => void;
};

/** FR-38: the payer records a cash payment for someone; confirmed at once. */
export function CashSheet({ open, onClose, rows, nameOf, currency, online, busy, onRecord }: CashSheetProps) {
  const [who, setWho] = useState<string | null>(null);
  const [amount, setAmount] = useState("");
  const [error, setError] = useState(false);
  const legendId = useId();
  useEffect(() => {
    if (!open) return;
    const first = rows[0];
    setWho(first?.key ?? null);
    setAmount(first ? centsToInput(first.owedCents) : "");
    setError(false);
  }, [open, rows]);
  const cents = parseCents(amount);

  function submit(event?: FormEvent<HTMLFormElement>): void {
    event?.preventDefault();
    if (!who || cents === null || cents <= 0) {
      setError(true);
      return;
    }
    onRecord(who, cents);
  }

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      title={S.cashTitle}
      footer={
        <PillButton
          fullWidth
          loading={busy}
          disabled={!online || !who}
          disabledReason={online ? undefined : OFFLINE}
          onClick={() => submit()}
        >
          {S.cashCta}
        </PillButton>
      }
    >
      <form className="kt-form" onSubmit={submit} noValidate>
        <fieldset className="kt-cash-who" aria-labelledby={legendId}>
          <span id={legendId} className="kt-field__label">
            {S.cashWho}
          </span>
          {rows.map((row) => (
            <label key={row.key} className="kt-cash-who__option">
              <input
                type="radio"
                name="cash-who"
                value={row.key}
                checked={who === row.key}
                onChange={() => {
                  setWho(row.key);
                  setAmount(centsToInput(row.owedCents));
                  setError(false);
                }}
              />
              <span>{nameOf(row)}</span>
              <span className="kt-money">{formatMoney(row.owedCents, currency)}</span>
            </label>
          ))}
        </fieldset>
        <AmountField
          value={amount}
          error={error}
          onChange={(value) => {
            setAmount(value);
            setError(false);
          }}
        />
      </form>
    </BottomSheet>
  );
}

export type SettleDialogProps = {
  open: boolean;
  onClose: () => void;
  outstanding: readonly { key: string; name: string; owedCents: number }[];
  payerName: string;
  currency: string;
  busy: boolean;
  onConfirm: () => void;
};

/** Settle dialog (PRD §14A): "Everyone's square." or who still owes + "This moves to house balances." */
export function SettleDialog({ open, onClose, outstanding, payerName, currency, busy, onConfirm }: SettleDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={S.settleTitle}
      confirmLabel={S.settleCta}
      onConfirm={onConfirm}
      confirmLoading={busy}
    >
      {outstanding.length === 0 ? (
        <p>{S.square}</p>
      ) : (
        <>
          <ul className="kt-settle-list">
            {outstanding.map((row) => (
              <li key={row.key}>
                {row.owedCents > 0
                  ? S.stillOwes(row.name, formatMoney(row.owedCents, currency))
                  : S.owesBack(payerName, row.name, formatMoney(-row.owedCents, currency))}
              </li>
            ))}
          </ul>
          <p>{S.movesToBalances}</p>
        </>
      )}
    </Dialog>
  );
}

/** Void dialog (PRD §14A): reason required, 3–200 characters, warn button "Void this tab". */
export function VoidDialog({
  open,
  onClose,
  busy,
  serverError,
  onConfirm,
}: {
  open: boolean;
  onClose: () => void;
  busy: boolean;
  serverError: string | null;
  onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState(false);
  const id = useId();
  const errorId = useId();
  useEffect(() => {
    if (!open) return;
    setReason("");
    setError(false);
  }, [open]);
  const clean = reason.split(/\s+/).filter(Boolean).join(" ");
  const message = error ? S.voidReasonError : serverError;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={S.voidTitle}
      variant="danger"
      confirmLabel={S.voidCta}
      confirmLoading={busy}
      onConfirm={() => {
        if (clean.length < 3 || clean.length > 200) {
          setError(true);
          return;
        }
        onConfirm(clean);
      }}
    >
      <p>{S.voidBody}</p>
      <div className="kt-field">
        <label htmlFor={id} className="kt-field__label">
          {S.voidReason}
        </label>
        <textarea
          id={id}
          className="kt-textarea"
          rows={3}
          maxLength={200}
          value={reason}
          aria-invalid={error || undefined}
          aria-describedby={message ? errorId : undefined}
          onChange={(event) => {
            setReason(event.target.value);
            setError(false);
          }}
        />
        {message ? (
          <p id={errorId} className="kt-field-error">
            {message}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}

/** "Reject asks for an optional note" (PRD §14A Confirm payment). */
export function RejectDialog({
  open,
  title,
  onClose,
  busy,
  onConfirm,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  busy: boolean;
  onConfirm: (note: string | null) => void;
}) {
  const [note, setNote] = useState("");
  const id = useId();
  useEffect(() => {
    if (open) setNote("");
  }, [open]);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      variant="danger"
      confirmLabel={S.rejectCta}
      confirmLoading={busy}
      onConfirm={() => onConfirm(note.trim() || null)}
    >
      <div className="kt-field">
        <label htmlFor={id} className="kt-field__label">
          {S.rejectNote}
        </label>
        <textarea
          id={id}
          className="kt-textarea"
          rows={2}
          maxLength={280}
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </div>
    </Dialog>
  );
}

/** "{name} just changed this" with theirs vs yours (PRD §21.3 guarded edits, SYNC-05). */
export function ConflictDialog({
  open,
  who,
  theirs,
  mine,
  currency,
  busy,
  onUseTheirs,
  onKeepMine,
}: {
  open: boolean;
  who: string;
  theirs: Pick<BillItem, "name" | "line_total_cents"> | null;
  mine: { name: string; line_total_cents: number } | null;
  currency: string;
  busy: boolean;
  onUseTheirs: () => void;
  onKeepMine: () => void;
}) {
  return (
    <Dialog
      open={open}
      onClose={onUseTheirs}
      title={strings.sync.justChanged(who)}
      confirmLabel={strings.sync.keepMine}
      cancelLabel={strings.sync.useTheirs}
      confirmLoading={busy}
      onConfirm={onKeepMine}
    >
      <dl className="kt-conflict">
        {theirs ? (
          <div className="kt-conflict__row">
            <dt>{strings.sync.theirs}</dt>
            <dd>
              {theirs.name} · <span className="kt-money">{formatMoney(theirs.line_total_cents, currency)}</span>
            </dd>
          </div>
        ) : null}
        {mine ? (
          <div className="kt-conflict__row">
            <dt>{strings.sync.yours}</dt>
            <dd>
              {mine.name} · <span className="kt-money">{formatMoney(mine.line_total_cents, currency)}</span>
            </dd>
          </div>
        ) : null}
      </dl>
    </Dialog>
  );
}

export type MenuAction = { key: string; label: string; onSelect?: () => void; href?: string; danger?: boolean; write?: boolean };

/** The ⋯ menu (PRD §14A 5 header): a sheet of actions. */
export function MenuSheet({
  open,
  onClose,
  actions,
  online,
}: {
  open: boolean;
  onClose: () => void;
  actions: readonly MenuAction[];
  online: boolean;
}) {
  return (
    <BottomSheet open={open} onClose={onClose} title={S.menu}>
      <ul className="kt-menu-list">
        {actions.map((action) => (
          <li key={action.key}>
            {action.href ? (
              <a className="kt-pill" data-variant="secondary" data-size={52} data-full="" href={action.href} download>
                <span>{action.label}</span>
              </a>
            ) : (
              <PillButton
                variant={action.danger ? "danger" : "secondary"}
                size={52}
                fullWidth
                disabled={Boolean(action.write) && !online}
                disabledReason={action.write && !online ? OFFLINE : undefined}
                onClick={() => action.onSelect?.()}
              >
                {action.label}
              </PillButton>
            )}
          </li>
        ))}
      </ul>
    </BottomSheet>
  );
}

/** Add a guest from the ⋯ menu: creates the guest column and shows their personal link. */
export function AddGuestSheet({
  open,
  onClose,
  online,
  onAdd,
}: {
  open: boolean;
  onClose: () => void;
  online: boolean;
  onAdd: (name: string) => Promise<{ link: string } | { error: string }>;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    setName("");
    setError(null);
    setLink(null);
    setCopied(false);
  }, [open]);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const clean = name.trim();
    if (!clean || busy) return;
    setBusy(true);
    setError(null);
    const out = await onAdd(clean);
    setBusy(false);
    if ("error" in out) setError(out.error);
    else {
      setLink(out.link);
      setName("");
    }
  }

  return (
    <BottomSheet open={open} onClose={onClose} title={S.guestTitle}>
      <form className="kt-form" onSubmit={(event) => void submit(event)}>
        <div className="kt-field">
          <label htmlFor={id} className="kt-field__label">
            {strings.split.guestName}
          </label>
          <input
            id={id}
            className="kt-input"
            value={name}
            maxLength={24}
            autoComplete="off"
            onChange={(event) => setName(event.target.value)}
          />
          {error ? <p className="kt-field-error">{error}</p> : null}
        </div>
        <PillButton
          type="submit"
          fullWidth
          loading={busy}
          disabled={!name.trim() || !online}
          disabledReason={online ? undefined : OFFLINE}
        >
          {strings.split.guestAdd}
        </PillButton>
        {link ? (
          <div className="kt-field">
            <span className="kt-field__label">{strings.split.guestLink}</span>
            <span className="kt-guest-link">{displayLink(link)}</span>
            <PillButton variant="secondary" size={52} onClick={() => void copyText(link).then(setCopied)}>
              {copied ? strings.shareScreen.copied : strings.shareScreen.copyLink}
            </PillButton>
          </div>
        ) : null}
      </form>
    </BottomSheet>
  );
}
