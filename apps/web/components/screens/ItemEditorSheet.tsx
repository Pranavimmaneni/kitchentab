"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { BottomSheet } from "@/components/BottomSheet";
import { PillButton } from "@/components/PillButton";
import type { BillItem } from "@/lib/api/types";
import { centsToInput, parseCents } from "@/lib/bills";
import { strings } from "@/lib/strings";

export type ItemDraft = {
  name: string;
  quantity: string | null;
  unit_price_cents: number | null;
  line_total_cents: number;
  discount_cents: number;
  taxable: boolean;
};

export type ItemEditorSheetProps = {
  open: boolean;
  /** The item being edited, or null to add a missed item. */
  item: BillItem | null;
  onClose: () => void;
  onSave: (draft: ItemDraft) => Promise<void>;
  onDelete?: () => Promise<void>;
  busy?: boolean;
  error?: string | null;
};

const S = strings.review.editor;

/** Item editor (PRD §14A 2): name, quantity, unit price, line total, discount, taxable, raw text. */
export function ItemEditorSheet({ open, item, onClose, onSave, onDelete, busy = false, error }: ItemEditorSheetProps) {
  const ids = {
    name: useId(),
    qty: useId(),
    unit: useId(),
    total: useId(),
    discount: useId(),
    nameErr: useId(),
    priceErr: useId(),
  };
  const nameRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [qty, setQty] = useState("1");
  const [unit, setUnit] = useState("");
  const [total, setTotal] = useState("");
  const [discount, setDiscount] = useState("");
  const [taxable, setTaxable] = useState(false);
  const [nameError, setNameError] = useState(false);
  const [priceError, setPriceError] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName(item?.name ?? "");
    setQty(item ? String(Number(item.quantity)) : "1");
    setUnit(item?.unit_price_cents != null ? centsToInput(item.unit_price_cents) : "");
    setTotal(item ? centsToInput(item.line_total_cents) : "");
    setDiscount(item?.discount_cents ? centsToInput(item.discount_cents) : "");
    setTaxable(item?.taxable ?? false);
    setNameError(false);
    setPriceError(false);
  }, [open, item]);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const cleanName = name.trim();
    const lineTotal = parseCents(total, { allowNegative: true });
    const unitPrice = unit.trim() ? parseCents(unit, { allowNegative: true }) : null;
    const discountCents = discount.trim() ? parseCents(discount) : 0;
    const quantity = /^\d{1,7}(\.\d{1,3})?$/.test(qty.trim()) && Number(qty) > 0 ? qty.trim() : null;
    setNameError(!cleanName);
    const badPrice = lineTotal === null || (unit.trim() !== "" && unitPrice === null) || discountCents === null;
    setPriceError(badPrice);
    if (!cleanName || badPrice || lineTotal === null || discountCents === null) return;
    await onSave({
      name: cleanName,
      quantity,
      unit_price_cents: unitPrice,
      line_total_cents: lineTotal,
      discount_cents: discountCents,
      taxable,
    });
  }

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      title={item ? S.editTitle : S.addTitle}
      initialFocusRef={nameRef}
    >
      <form className="kt-form" onSubmit={(event) => void submit(event)} noValidate>
        <div className="kt-field">
          <label htmlFor={ids.name} className="kt-field__label">
            {S.name}
          </label>
          <input
            ref={nameRef}
            id={ids.name}
            className="kt-input"
            value={name}
            maxLength={120}
            autoComplete="off"
            aria-invalid={nameError || undefined}
            aria-describedby={nameError ? ids.nameErr : undefined}
            onChange={(event) => setName(event.target.value)}
          />
          {nameError ? (
            <p id={ids.nameErr} className="kt-field-error">
              {S.nameError}
            </p>
          ) : null}
        </div>
        <div className="kt-form__grid">
          <div className="kt-field">
            <label htmlFor={ids.qty} className="kt-field__label">
              {S.quantity}
            </label>
            <input
              id={ids.qty}
              className="kt-input kt-money"
              inputMode="decimal"
              value={qty}
              onChange={(event) => setQty(event.target.value)}
            />
          </div>
          <div className="kt-field">
            <label htmlFor={ids.unit} className="kt-field__label">
              {S.unitPrice}
            </label>
            <input
              id={ids.unit}
              className="kt-input kt-money"
              inputMode="decimal"
              value={unit}
              onChange={(event) => setUnit(event.target.value)}
            />
          </div>
          <div className="kt-field">
            <label htmlFor={ids.total} className="kt-field__label">
              {S.lineTotal}
            </label>
            <input
              id={ids.total}
              className="kt-input kt-money"
              inputMode="decimal"
              value={total}
              aria-invalid={priceError || undefined}
              aria-describedby={priceError ? ids.priceErr : undefined}
              onChange={(event) => setTotal(event.target.value)}
            />
          </div>
          <div className="kt-field">
            <label htmlFor={ids.discount} className="kt-field__label">
              {S.discount}
            </label>
            <input
              id={ids.discount}
              className="kt-input kt-money"
              inputMode="decimal"
              value={discount}
              onChange={(event) => setDiscount(event.target.value)}
            />
          </div>
        </div>
        {priceError ? (
          <p id={ids.priceErr} className="kt-field-error">
            {S.priceError}
          </p>
        ) : null}
        <label className="kt-checkbox-row">
          <input type="checkbox" checked={taxable} onChange={(event) => setTaxable(event.target.checked)} />
          {S.taxable}
        </label>
        {item?.raw_text ? (
          <div className="kt-field">
            <span className="kt-field__label">{S.rawText}</span>
            <p className="kt-raw-text">{item.raw_text}</p>
          </div>
        ) : null}
        {error ? (
          <p className="kt-field-error" role="alert">
            {error}
          </p>
        ) : null}
        <PillButton type="submit" fullWidth loading={busy}>
          {S.save}
        </PillButton>
        {item && onDelete ? (
          <PillButton variant="danger" size={52} fullWidth onClick={() => void onDelete()} disabled={busy}>
            {S.delete}
          </PillButton>
        ) : null}
      </form>
    </BottomSheet>
  );
}
