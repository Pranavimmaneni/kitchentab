"use client";

import { useId, useState, type FormEvent } from "react";
import { newId } from "@/lib/api/client";
import type { KitchenPlace, Place } from "@/lib/api/kitchen-types";
import type { Member } from "@/lib/api/types";
import type { LeftoversBody } from "@/lib/kitchen-writes";
import type { FoodIconKey } from "@/lib/icons/food";
import { strings } from "@/lib/strings";
import { BottomSheet } from "./BottomSheet";
import { FoodIcon, foodPastel } from "./FoodIcon";
import { OwnerPicker, PlacePicker } from "./ItemSheet";
import { PillButton } from "./PillButton";
import { Switch } from "./Switch";

/** A short pick of icons per place; the server falls back to leftovers / generic_pantry. */
const ICON_CHOICES: Record<KitchenPlace, readonly FoodIconKey[]> = {
  fridge: [
    "leftovers",
    "pizza",
    "dumplings",
    "chicken",
    "fish",
    "cheese",
    "sauce_jar",
    "generic_fridge",
  ],
  pantry: ["generic_pantry", "bread", "cookies", "chips", "pasta", "canned", "spices", "banana"],
};

/**
 * "+ Add leftovers" (fridge) / "+ Add something" (pantry) (FR-79, KITCH-13): name, icon, place,
 * owners (default: me), optional shelf life. No bill and no money.
 */
export function LeftoversSheet({
  open,
  onClose,
  place,
  meMemberId,
  members,
  onAdd,
}: {
  open: boolean;
  onClose: () => void;
  place: KitchenPlace;
  meMemberId: string;
  members: readonly Member[];
  onAdd: (body: LeftoversBody, key: string) => Promise<void>;
}) {
  return (
    <BottomSheet open={open} onClose={onClose} title={strings.leftovers.titles[place]}>
      {open ? (
        <LeftoversForm place={place} meMemberId={meMemberId} members={members} onAdd={onAdd} />
      ) : null}
    </BottomSheet>
  );
}

function LeftoversForm({
  place,
  meMemberId,
  members,
  onAdd,
}: {
  place: KitchenPlace;
  meMemberId: string;
  members: readonly Member[];
  onAdd: (body: LeftoversBody, key: string) => Promise<void>;
}) {
  const nameId = useId();
  const daysId = useId();
  const errorId = useId();
  const iconLabelId = useId();
  const [name, setName] = useState("");
  const [where, setWhere] = useState<Place>(place);
  const [icon, setIcon] = useState<FoodIconKey>(ICON_CHOICES[place][0]!);
  const [days, setDays] = useState("");
  const [owners, setOwners] = useState<string[]>([meMemberId]);
  const [openToShare, setOpenToShare] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [key] = useState(newId);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return setError(strings.leftovers.nameError);
    const n = days.trim() ? Number(days) : null;
    if (n !== null && (!/^\d+$/.test(days.trim()) || n < 1 || n > 3650))
      return setError(strings.leftovers.daysError);
    setError(null);
    setSaving(true);
    try {
      await onAdd(
        {
          name: trimmed,
          place: where,
          icon_key: icon,
          ...(n !== null ? { shelf_life_days: n } : {}),
          owner_member_ids: owners.length ? owners : [meMemberId],
          open_to_share: openToShare,
        },
        key,
      );
    } catch {
      setSaving(false);
    }
  }

  return (
    <form className="kt-form" onSubmit={(e) => void submit(e)} noValidate>
      <div className="kt-field">
        <label htmlFor={nameId} className="kt-field__label">
          {strings.leftovers.name}
        </label>
        <input
          id={nameId}
          className="kt-input kt-input--big"
          value={name}
          maxLength={80}
          placeholder={strings.leftovers.namePlaceholder}
          aria-invalid={error === strings.leftovers.nameError || undefined}
          aria-describedby={error ? errorId : undefined}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <div className="kt-field">
        <span id={iconLabelId} className="kt-field__label">
          {strings.leftovers.icon}
        </span>
        <div className="kt-icon-choices" role="radiogroup" aria-labelledby={iconLabelId}>
          {ICON_CHOICES[place].map((key) => (
            <button
              key={key}
              type="button"
              role="radio"
              aria-checked={icon === key}
              aria-label={strings.leftovers.iconNames[key] ?? key}
              className="kt-icon-choice"
              onClick={() => setIcon(key)}
            >
              <span className="kt-food" style={{ background: foodPastel(key) }}>
                <FoodIcon iconKey={key} />
              </span>
            </button>
          ))}
        </div>
      </div>
      <PlacePicker
        value={where}
        onChange={setWhere}
        label={strings.leftovers.where}
        places={
          place === "pantry" ? ["pantry", "fridge", "freezer"] : ["fridge", "freezer", "pantry"]
        }
      />
      <div className="kt-field">
        <label htmlFor={daysId} className="kt-field__label">
          {strings.leftovers.shelfLife}
        </label>
        <input
          id={daysId}
          className="kt-input kt-input--big"
          inputMode="numeric"
          value={days}
          aria-invalid={error === strings.leftovers.daysError || undefined}
          aria-describedby={error ? errorId : undefined}
          onChange={(e) => setDays(e.target.value)}
        />
      </div>
      <OwnerPicker members={members} value={owners} onChange={setOwners} />
      <div className="kt-share-row">
        <span className="kt-share-row__title">{strings.leftovers.open}</span>
        <Switch checked={openToShare} label={strings.leftovers.open} onChange={setOpenToShare} />
      </div>
      {error ? (
        <p id={errorId} className="kt-field-error" role="alert">
          {error}
        </p>
      ) : null}
      <PillButton type="submit" fullWidth loading={saving}>
        {strings.leftovers.add}
      </PillButton>
    </form>
  );
}
