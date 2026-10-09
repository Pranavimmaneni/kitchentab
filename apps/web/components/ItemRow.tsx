"use client";

import { useId, useState } from "react";
import { formatMoney } from "@/lib/format";
import { strings } from "@/lib/strings";
import { PencilIcon } from "./icons";
import { StampChip } from "./StampChip";

export type Storage = "fridge" | "freezer" | "pantry" | "none";

export const STORAGE_ORDER: readonly Storage[] = ["fridge", "freezer", "pantry", "none"];

export function nextStorage(current: Storage): Storage {
  return STORAGE_ORDER[(STORAGE_ORDER.indexOf(current) + 1) % STORAGE_ORDER.length]!;
}

export type ItemRowProps = {
  name: string;
  priceCents: number;
  /** Extraction is unsure about this line: shows the CHECK? stamp. */
  flagged?: boolean;
  storage: Storage;
  onStorageChange?: (next: Storage) => void;
  onEdit?: () => void;
  /** Inline edit state; the caller parses and validates the raw values. */
  editing?: boolean;
  onSave?: (raw: { name: string; price: string }) => void;
  onCancel?: () => void;
  currency?: string;
};

/** Review line: name, CHECK? stamp, storage tag, price, edit (PRD §14B, §19.8). */
export function ItemRow({
  name,
  priceCents,
  flagged = false,
  storage,
  onStorageChange,
  onEdit,
  editing = false,
  onSave,
  onCancel,
  currency = "USD",
}: ItemRowProps) {
  const nameId = useId();
  const priceId = useId();
  const price = formatMoney(priceCents, currency);
  const [draftName, setDraftName] = useState(name);
  const [draftPrice, setDraftPrice] = useState(price.replace(/[^0-9.]/g, ""));
  const place = strings.review.storage[storage];

  if (editing) {
    return (
      <form
        className="kt-item-row kt-item-row--editing"
        onSubmit={(event) => {
          event.preventDefault();
          onSave?.({ name: draftName, price: draftPrice });
        }}
      >
        <label htmlFor={nameId} className="sr-only">
          {strings.review.nameField}
        </label>
        <input id={nameId} className="kt-input kt-item-row__name-input" value={draftName} onChange={(e) => setDraftName(e.target.value)} />
        <label htmlFor={priceId} className="sr-only">
          {strings.review.priceField}
        </label>
        <input
          id={priceId}
          className="kt-input kt-item-row__price-input kt-money"
          inputMode="decimal"
          value={draftPrice}
          onChange={(e) => setDraftPrice(e.target.value)}
        />
        <button type="submit" className="kt-text-button">
          {strings.review.save}
        </button>
        <button type="button" className="kt-text-button" onClick={onCancel}>
          {strings.review.cancel}
        </button>
      </form>
    );
  }

  return (
    <div className="kt-item-row" data-flagged={flagged || undefined}>
      <span className="kt-item-row__name">{name}</span>
      {flagged ? <StampChip rotate={-3}>{strings.review.check}</StampChip> : null}
      <button
        type="button"
        className="kt-item-row__tag-button"
        aria-label={strings.review.storageLabel(name, place)}
        onClick={() => onStorageChange?.(nextStorage(storage))}
      >
        <span className="kt-storage-tag" data-storage={storage}>
          {place}
        </span>
      </button>
      <span className="kt-item-row__price kt-money">{price}</span>
      <button type="button" className="kt-item-row__edit" aria-label={strings.review.edit(name)} onClick={onEdit}>
        <PencilIcon size={18} />
      </button>
    </div>
  );
}
