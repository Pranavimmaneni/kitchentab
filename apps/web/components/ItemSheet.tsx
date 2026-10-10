"use client";

import { useId, useState, type FormEvent } from "react";
import { newId } from "@/lib/api/client";
import type { AskAmount, InventoryItem, Place } from "@/lib/api/kitchen-types";
import type { Member } from "@/lib/api/types";
import {
  askLabel,
  askNote,
  askedLabel,
  coOwnerNote,
  helpNote,
  NOTE_MAX,
  sheetMode,
  sourceLine,
} from "@/lib/kitchen";
import type { ItemEditBody } from "@/lib/kitchen-writes";
import { colorIndexOf } from "@/lib/bills";
import { strings } from "@/lib/strings";
import { BottomSheet } from "./BottomSheet";
import { FoodIcon, foodPastel } from "./FoodIcon";
import { MemberAvatar } from "./MemberAvatar";
import { PillButton } from "./PillButton";
import { StampChip } from "./StampChip";
import { Switch } from "./Switch";

const AMOUNTS: readonly AskAmount[] = ["a_bit", "half", "all"];
const PLACES: readonly Place[] = ["fridge", "freezer", "pantry"];

export type ItemSheetActions = {
  setSharing: (item: InventoryItem, open: boolean) => void;
  usedUp: (item: InventoryItem) => Promise<void>;
  stillGood: (item: InventoryItem) => Promise<void>;
  edit: (item: InventoryItem, body: ItemEditBody) => Promise<void>;
  ask: (item: InventoryItem, amount: AskAmount, note: string, key: string) => Promise<void>;
  cancel: (item: InventoryItem, requestId: string) => Promise<void>;
};

export type ItemSheetProps = {
  item: InventoryItem | null;
  meMemberId: string;
  /** Active house members, for editing the owners of manual items. */
  members: readonly Member[];
  onClose: () => void;
  actions: ItemSheetActions;
};

/**
 * Item sheet (PRD §19.8, FR-68): icon, name, source, WHO OWNS IT, then the viewer's action set:
 * (a) owner: sharing switch with the co-owner note, Used up, Still good when due, Edit;
 * (b) open for me: HELP YOURSELF; (c) locked: HOW MUCH? + note + Ask; (d) asked: waiting + Cancel.
 * The API decides who may do what; the sheet only shows the controls it would accept.
 */
export function ItemSheet({ item, meMemberId, members, onClose, actions }: ItemSheetProps) {
  return (
    <BottomSheet open={item !== null} onClose={onClose} title={item?.name ?? ""}>
      {item ? (
        <SheetBody
          key={item.id}
          item={item}
          meMemberId={meMemberId}
          members={members}
          actions={actions}
        />
      ) : null}
    </BottomSheet>
  );
}

function SheetBody({
  item,
  meMemberId,
  members,
  actions,
}: {
  item: InventoryItem;
  meMemberId: string;
  members: readonly Member[];
  actions: ItemSheetActions;
}) {
  const [editing, setEditing] = useState(false);
  const mode = sheetMode(item);
  return (
    <div className="kt-item-sheet">
      <div className="kt-item-sheet__top">
        <span
          className="kt-food kt-food--big"
          style={{ background: foodPastel(item.icon_key, item.place) }}
        >
          <FoodIcon iconKey={item.icon_key} place={item.place} size={40} strokeWidth={1.5} />
        </span>
        <span className="kt-item-sheet__source">{sourceLine(item)}</span>
      </div>

      <div className="kt-field">
        <span className="kt-field__label">{strings.item.whoOwns}</span>
        <ul className="kt-owner-chips">
          {item.owners.map((o, i) => (
            <li key={o.id} className="kt-owner-chip">
              <MemberAvatar
                name={o.display_name}
                colorIndex={colorIndexOf({ color: o.color, sort_order: i })}
                size={26}
              />
              {o.id === meMemberId ? strings.place.you : o.display_name}
            </li>
          ))}
        </ul>
      </div>

      {mode === "owner" ? (
        editing ? (
          <EditForm
            item={item}
            members={members}
            onDone={() => setEditing(false)}
            edit={actions.edit}
          />
        ) : (
          <OwnerActions
            item={item}
            meMemberId={meMemberId}
            actions={actions}
            onEdit={() => setEditing(true)}
          />
        )
      ) : null}
      {mode === "open" ? (
        <div className="kt-help">
          <StampChip rotate={-3}>{strings.item.help}</StampChip>
          <span className="kt-help__note">{helpNote(item)}</span>
        </div>
      ) : null}
      {mode === "locked" ? <AskForm item={item} ask={actions.ask} /> : null}
      {mode === "asked" && item.my_request ? (
        <>
          <div className="kt-help" data-asked="">
            <span
              className="kt-stamp"
              data-tone="violet"
              style={{ ["--stamp-rotate" as string]: "-2deg" }}
            >
              {askedLabel(item)}
            </span>
            <span className="kt-help__note">{strings.item.askedNote}</span>
          </div>
          <CancelButton onCancel={() => actions.cancel(item, item.my_request!.id)} />
        </>
      ) : null}
    </div>
  );
}

function OwnerActions({
  item,
  meMemberId,
  actions,
  onEdit,
}: {
  item: InventoryItem;
  meMemberId: string;
  actions: ItemSheetActions;
  onEdit: () => void;
}) {
  const noteId = useId();
  const [busy, setBusy] = useState<"used" | "good" | null>(null);
  const mineOpen = item.owners.find((o) => o.id === meMemberId)?.open_to_share ?? false;
  const due = item.use_soon || item.probably_gone;
  async function run(kind: "used" | "good", fn: () => Promise<void>) {
    setBusy(kind);
    try {
      await fn();
    } finally {
      setBusy(null);
    }
  }
  return (
    <>
      <div className="kt-share-row">
        <div className="kt-share-row__text">
          <span className="kt-share-row__title">{strings.item.openToShare}</span>
          <span id={noteId} className="kt-share-row__note">
            {coOwnerNote(item, meMemberId)}
          </span>
        </div>
        <Switch
          checked={mineOpen}
          label={strings.item.openToShare}
          describedBy={noteId}
          onChange={(next) => actions.setSharing(item, next)}
        />
      </div>
      {item.probably_gone ? (
        <p className="kt-still-there">
          <strong>{strings.item.stillThere}</strong> {strings.item.probably}
        </p>
      ) : null}
      <div className="kt-two-up">
        <PillButton
          variant="secondary"
          size={52}
          loading={busy === "used"}
          onClick={() => void run("used", () => actions.usedUp(item))}
        >
          {strings.item.usedUp}
        </PillButton>
        {due ? (
          <PillButton
            variant="secondary"
            size={52}
            className="kt-pill--soon"
            loading={busy === "good"}
            onClick={() => void run("good", () => actions.stillGood(item))}
          >
            {strings.item.stillGood}
          </PillButton>
        ) : null}
      </div>
      <PillButton variant="dashed" fullWidth onClick={onEdit}>
        {strings.item.edit}
      </PillButton>
    </>
  );
}

function EditForm({
  item,
  members,
  onDone,
  edit,
}: {
  item: InventoryItem;
  members: readonly Member[];
  onDone: () => void;
  edit: ItemSheetActions["edit"];
}) {
  const nameId = useId();
  const daysId = useId();
  const errorId = useId();
  const [name, setName] = useState(item.name);
  const [place, setPlace] = useState<Place>(item.place);
  const [days, setDays] = useState(String(item.shelf_life_days));
  const [owners, setOwners] = useState<string[]>(item.owners.map((o) => o.id));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const manual = item.source === null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    const n = Number(days);
    if (!trimmed) return setError(strings.leftovers.nameError);
    if (!/^\d+$/.test(days.trim()) || n < 1 || n > 3650)
      return setError(strings.leftovers.daysError);
    const body: ItemEditBody = {};
    if (trimmed !== item.name) body.name = trimmed;
    if (place !== item.place) body.place = place;
    if (n !== item.shelf_life_days) body.shelf_life_days = n;
    const before = item.owners
      .map((o) => o.id)
      .sort()
      .join();
    if (manual && owners.slice().sort().join() !== before) body.owner_member_ids = owners;
    setSaving(true);
    setError(null);
    try {
      if (Object.keys(body).length) await edit(item, body);
      onDone();
    } catch {
      setSaving(false);
    }
  }

  return (
    <form className="kt-form" onSubmit={(e) => void submit(e)} noValidate>
      <div className="kt-field">
        <label htmlFor={nameId} className="kt-field__label">
          {strings.item.name}
        </label>
        <input
          id={nameId}
          className="kt-input kt-input--big"
          value={name}
          maxLength={80}
          onChange={(e) => setName(e.target.value)}
          aria-describedby={error ? errorId : undefined}
        />
      </div>
      <PlacePicker value={place} onChange={setPlace} label={strings.item.where} />
      <div className="kt-field">
        <label htmlFor={daysId} className="kt-field__label">
          {strings.item.shelfLife}
        </label>
        <input
          id={daysId}
          className="kt-input kt-input--big"
          inputMode="numeric"
          value={days}
          onChange={(e) => setDays(e.target.value)}
          aria-invalid={error === strings.leftovers.daysError || undefined}
          aria-describedby={error ? errorId : undefined}
        />
      </div>
      {manual ? <OwnerPicker members={members} value={owners} onChange={setOwners} /> : null}
      {error ? (
        <p id={errorId} className="kt-field-error" role="alert">
          {error}
        </p>
      ) : null}
      <PillButton type="submit" size={52} fullWidth loading={saving}>
        {strings.item.save}
      </PillButton>
    </form>
  );
}

export function PlacePicker({
  value,
  onChange,
  label,
  places = PLACES,
}: {
  value: Place;
  onChange: (place: Place) => void;
  label: string;
  places?: readonly Place[];
}) {
  const labelId = useId();
  return (
    <div className="kt-field">
      <span id={labelId} className="kt-field__label">
        {label}
      </span>
      <div className="kt-choice-row" role="radiogroup" aria-labelledby={labelId}>
        {places.map((p) => (
          <button
            key={p}
            type="button"
            role="radio"
            aria-checked={value === p}
            className="kt-choice"
            onClick={() => onChange(p)}
          >
            {strings.item.places[p]}
          </button>
        ))}
      </div>
    </div>
  );
}

export function OwnerPicker({
  members,
  value,
  onChange,
}: {
  members: readonly Member[];
  value: readonly string[];
  onChange: (ids: string[]) => void;
}) {
  const labelId = useId();
  return (
    <fieldset className="kt-field kt-owner-picker" aria-labelledby={labelId}>
      <span id={labelId} className="kt-field__label">
        {strings.item.owners}
      </span>
      {members.map((m) => {
        const checked = value.includes(m.id);
        return (
          <label key={m.id} className="kt-checkbox-row">
            <input
              type="checkbox"
              checked={checked}
              onChange={() =>
                onChange(checked ? value.filter((id) => id !== m.id) : [...value, m.id])
              }
            />
            <MemberAvatar name={m.display_name} colorIndex={colorIndexOf(m)} size={26} />
            {m.display_name}
          </label>
        );
      })}
    </fieldset>
  );
}

function AskForm({ item, ask }: { item: InventoryItem; ask: ItemSheetActions["ask"] }) {
  const labelId = useId();
  const noteId = useId();
  const countId = useId();
  const [amount, setAmount] = useState<AskAmount>("a_bit");
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);
  // One key per ask, reused if the same ask is retried (PRD §21.3 creates).
  const [key, setKey] = useState(newId);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSending(true);
    try {
      await ask(item, amount, note, key);
    } finally {
      setKey(newId());
      setSending(false);
    }
  }

  return (
    <form className="kt-form" onSubmit={(e) => void submit(e)}>
      <p className="kt-ask-note">{askNote(item)}</p>
      <div className="kt-field">
        <span id={labelId} className="kt-field__label">
          {strings.item.howMuch}
        </span>
        <div className="kt-choice-row" role="radiogroup" aria-labelledby={labelId}>
          {AMOUNTS.map((a) => (
            <button
              key={a}
              type="button"
              role="radio"
              aria-checked={amount === a}
              className="kt-choice"
              onClick={() => setAmount(a)}
            >
              {strings.item.amounts[a]}
            </button>
          ))}
        </div>
      </div>
      <div className="kt-field">
        <label htmlFor={noteId} className="kt-field__label">
          {strings.item.note}
        </label>
        <input
          id={noteId}
          className="kt-input kt-input--big"
          placeholder={strings.item.notePlaceholder}
          value={note}
          maxLength={NOTE_MAX}
          aria-describedby={countId}
          onChange={(e) => setNote(e.target.value.slice(0, NOTE_MAX))}
        />
        <span id={countId} className="kt-count">
          {strings.item.noteCount(note.length)}
        </span>
      </div>
      <PillButton type="submit" fullWidth loading={sending}>
        {askLabel(item)}
      </PillButton>
    </form>
  );
}

function CancelButton({ onCancel }: { onCancel: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  return (
    <PillButton
      variant="secondary"
      size={52}
      fullWidth
      loading={busy}
      onClick={() => {
        setBusy(true);
        void onCancel().finally(() => setBusy(false));
      }}
    >
      {strings.item.cancel}
    </PillButton>
  );
}
