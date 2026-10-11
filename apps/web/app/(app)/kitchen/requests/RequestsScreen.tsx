"use client";

import { useCallback, useId, useMemo, useState, type ReactNode } from "react";
import { AppHeader } from "@/components/AppHeader";
import { FoodIcon, foodPastel } from "@/components/FoodIcon";
import { MemberAvatar } from "@/components/MemberAvatar";
import { PillButton } from "@/components/PillButton";
import { SegmentedControl } from "@/components/SegmentedControl";
import { Skeleton } from "@/components/Skeleton";
import { Toast } from "@/components/Toast";
import { ErrorCard } from "@/components/screens/ErrorCard";
import { HouseGate, type HouseContext } from "@/components/screens/HouseGate";
import { isApiError, newId } from "@/lib/api/client";
import { useHouse } from "@/lib/api/hooks";
import { useAnswerPreview, useRequests } from "@/lib/api/kitchen-hooks";
import type { AnswerOption, ShareRequest } from "@/lib/api/kitchen-types";
import { centsToInput, colorIndexOf } from "@/lib/bills";
import { formatMoney } from "@/lib/format";
import {
  ageLabel,
  canUndo,
  coOwnersLine,
  doneNote,
  itemWords,
  outgoingText,
  PART_PRESETS,
  partBody,
  requestPrice,
  requestStamp,
  splitDeltaChips,
  type PartChoice,
  type PartUnit,
  type Stamp,
} from "@/lib/kitchen";
import { useKitchenWrites, type KitchenWrites } from "@/lib/kitchen-writes";
import { strings } from "@/lib/strings";
import { useDebounced } from "@/lib/use-debounced";
import { useServerNow } from "@/lib/use-server-time";

type Box = "in" | "out";

/** K4 · Can I have some? (Requests.dc.html, FR-72..FR-76). */
export function RequestsScreen() {
  return (
    <HouseGate title={strings.requests.title} nav={false}>
      {(ctx) => <Requests {...ctx} />}
    </HouseGate>
  );
}

/** Re-renders every 30 s so ages and undo windows stay current; on the server's clock. */
function useNow(): Date {
  const now = useServerNow(30_000);
  return useMemo(() => new Date(now), [now]);
}

function Requests({ house }: HouseContext) {
  const [box, setBox] = useState<Box>("in");
  const query = useRequests(house.id, box);
  const houseView = useHouse(house.id);
  const writes = useKitchenWrites(house.id, house.member_id);
  const now = useNow();
  const [toast, setToast] = useState<{ message: string; variant?: "info" | "error" } | null>(null);
  const fail = useCallback(
    (error: unknown) =>
      setToast({
        message: isApiError(error) ? error.title : strings.item.couldntSave,
        variant: "error",
      }),
    [],
  );
  const names = Object.fromEntries(
    (houseView.data?.members ?? []).map((m) => [m.id, m.display_name]),
  );
  const counts = query.data?.counts;

  return (
    <div className="kt-screen kt-requests">
      <AppHeader
        back={{ href: "/", label: strings.place.back }}
        stepLabel={strings.requests.step(house.name)}
        title={strings.requests.title}
      />
      <div className="kt-requests__tabs">
        <SegmentedControl<Box>
          label={strings.requests.tabsLabel}
          value={box}
          onChange={setBox}
          options={[
            { value: "in", label: strings.requests.forYou(counts?.in ?? 0) },
            { value: "out", label: strings.requests.youAsked(counts?.out ?? 0) },
          ]}
        />
      </div>
      <main
        className="kt-screen__main"
        role="tabpanel"
        aria-label={
          box === "in"
            ? strings.requests.forYou(counts?.in ?? 0)
            : strings.requests.youAsked(counts?.out ?? 0)
        }
      >
        {query.isError ? (
          <ErrorCard error={query.error} onRetry={() => void query.refetch()} />
        ) : null}
        {query.isPending ? <Skeleton variant="card" lines={4} /> : null}
        {query.data && query.data.box === box ? (
          query.data.requests.length === 0 ? (
            <p className="kt-footnote">
              {box === "in" ? strings.requests.emptyIn : strings.requests.emptyOut}
            </p>
          ) : (
            <ul className="kt-list kt-requests__list">
              {query.data.requests.map((req) => (
                <li key={req.id}>
                  {box === "in" ? (
                    <IncomingCard
                      req={req}
                      houseId={house.id}
                      meMemberId={house.member_id}
                      names={names}
                      now={now}
                      writes={writes}
                      onError={fail}
                    />
                  ) : (
                    <OutgoingCard req={req} now={now} writes={writes} onError={fail} />
                  )}
                </li>
              ))}
            </ul>
          )
        ) : null}
        <p className="kt-mono-note">
          {strings.requests.footnote1}
          <br />
          {strings.requests.footnote2}
        </p>
      </main>
      <Toast
        message={toast?.message ?? null}
        variant={toast?.variant}
        onDismiss={() => setToast(null)}
      />
    </div>
  );
}

function StampText({ stamp }: { stamp: Stamp }) {
  return (
    <span
      className="kt-stamp"
      data-tone={stamp.tone}
      style={{ ["--stamp-rotate" as string]: "-3deg" }}
    >
      {stamp.text}
    </span>
  );
}

/** The item icon with its full price underneath (FR-82): the bill line total, or NO PRICE. */
function ItemBubble({ req, size = 44 }: { req: ShareRequest; size?: number }) {
  const price = requestPrice(req);
  return (
    <span className="kt-ask-item">
      <span
        className="kt-food kt-food--bubble"
        style={{ background: foodPastel(req.item.icon_key), width: size, height: size }}
      >
        <FoodIcon iconKey={req.item.icon_key} size={28} strokeWidth={1.5} />
      </span>
      <span
        className="kt-ask-item__price"
        data-testid="request-price"
        aria-label={strings.requests.priceLabel(price)}
      >
        {price}
      </span>
    </span>
  );
}

function IncomingCard({
  req,
  houseId,
  meMemberId,
  names,
  now,
  writes,
  onError,
}: {
  req: ShareRequest;
  houseId: string;
  meMemberId: string;
  names: Record<string, string>;
  now: Date;
  writes: KitchenWrites;
  onError: (error: unknown) => void;
}) {
  const who = req.requester.display_name ?? strings.requests.someone;
  const pending = req.status === "pending";
  return (
    <article
      className="kt-card kt-ask-card"
      data-status={req.status}
      aria-label={`${who}${strings.requests.wants(req.amount_words)}${itemWords(req)}`}
    >
      <div className="kt-ask-card__top">
        <MemberAvatar
          name={who}
          colorIndex={colorIndexOf({ color: req.requester.color ?? "", sort_order: 0 })}
          initial={req.requester.initial}
          size={36}
        />
        <div className="kt-ask-card__text">
          <p className="kt-ask-card__line">
            <b>{who}</b>
            {strings.requests.wants(req.amount_words)}
            <b>{itemWords(req)}</b>
          </p>
          {req.note ? <q className="kt-ask-card__quote">{req.note}</q> : null}
          <span className="kt-ask-card__age">{ageLabel(req.created_at, now)}</span>
        </div>
        <ItemBubble req={req} />
      </div>
      {req.co_owners ? <p className="kt-ask-card__co">{coOwnersLine(req.co_owners)}</p> : null}
      {pending ? (
        <AnswerForm
          req={req}
          houseId={houseId}
          meMemberId={meMemberId}
          names={names}
          writes={writes}
          onError={onError}
        />
      ) : (
        <Answered req={req} meMemberId={meMemberId} now={now} writes={writes} onError={onError} />
      )}
    </article>
  );
}

function AnswerForm({
  req,
  houseId,
  meMemberId,
  names,
  writes,
  onError,
}: {
  req: ShareRequest;
  houseId: string;
  meMemberId: string;
  names: Record<string, string>;
  writes: KitchenWrites;
  onError: (error: unknown) => void;
}) {
  const labelId = useId();
  const options: AnswerOption[] = req.options ?? ["free"];
  const [choice, setChoice] = useState<AnswerOption>("free");
  const [part, setPart] = useState<PartChoice>("base");
  const [unit, setUnit] = useState<PartUnit>("dollars");
  const [raw, setRaw] = useState(centsToInput(req.chip_in_suggestion_cents ?? 0));
  const [busy, setBusy] = useState<"yes" | "no" | null>(null);
  const [key, setKey] = useState(newId);
  const lineTotal = req.line_total_cents ?? 0;
  const who = req.requester.display_name ?? strings.requests.someone;
  const store = req.source?.store_name ?? strings.bill.untitled;
  const paying = choice === "split" || choice === "chip_in";
  const decision = choice === "chip_in" ? "chip_in" : "split";
  const body = paying
    ? partBody(decision, part, unit, raw, lineTotal, req.chip_in_suggestion_cents ?? null)
    : {};
  const invalid = body === null;
  // Equal share and the suggestion come with the card; any other part asks the server (FR-81).
  const custom = paying && part !== "base";
  const previewBody = useDebounced(body, part === "custom" ? 300 : 0);
  const preview = useAnswerPreview(houseId, req.id, decision, previewBody, custom);
  const ready =
    !custom || (JSON.stringify(previewBody) === JSON.stringify(body) && preview.isSuccess);
  const deltas =
    custom && choice === "split" ? (preview.data?.deltas ?? {}) : (req.split_deltas ?? {});
  const askerCents = custom
    ? (preview.data?.asker_cents ?? null)
    : choice === "chip_in"
      ? (req.chip_in_suggestion_cents ?? null)
      : null;

  async function answer(next: "decline" | AnswerOption) {
    if (next !== "decline" && body === null) return;
    setBusy(next === "decline" ? "no" : "yes");
    try {
      await writes.answer(req, next, body ?? {}, `${key}:${next}`);
    } catch (error) {
      onError(error);
      setBusy(null);
      setKey(newId());
    }
  }

  return (
    <>
      <span id={labelId} className="kt-field__label">
        {strings.requests.howShould}
      </span>
      <div className="kt-radio-cards" role="radiogroup" aria-labelledby={labelId}>
        <RadioCard
          checked={choice === "free"}
          onSelect={() => setChoice("free")}
          title={strings.requests.free}
        >
          {strings.requests.freeNote}
        </RadioCard>
        {options.includes("split") ? (
          <RadioCard
            checked={choice === "split"}
            onSelect={() => setChoice("split")}
            title={strings.requests.split}
          >
            <span>{strings.requests.splitWhy(who, store)}</span>
            <span className="kt-delta-chips" data-testid="split-deltas">
              {splitDeltaChips(deltas, names, meMemberId).map((d) => (
                <span key={d} className="kt-delta-chip">
                  {d}
                </span>
              ))}
            </span>
          </RadioCard>
        ) : null}
        {options.includes("chip_in") ? (
          <RadioCard
            checked={choice === "chip_in"}
            onSelect={() => setChoice("chip_in")}
            title={strings.requests.chipIn}
          >
            {strings.requests.chipInWhy(store)}
          </RadioCard>
        ) : null}
      </div>
      {paying ? (
        <PartPicker
          decision={decision}
          part={part}
          unit={unit}
          raw={raw}
          lineTotal={lineTotal}
          invalid={invalid}
          onPart={setPart}
          onUnit={(next) => {
            setUnit(next);
            setRaw(next === "percent" ? "" : centsToInput(req.chip_in_suggestion_cents ?? 0));
          }}
          onRaw={setRaw}
        />
      ) : null}
      {paying && !invalid && (custom || askerCents !== null) ? (
        <p className="kt-ask-card__part" data-testid="part-summary" aria-live="polite">
          {!ready || askerCents === null
            ? strings.requests.partWorking
            : choice === "chip_in"
              ? strings.requests.partOwes(who, formatMoney(askerCents))
              : strings.requests.partPays(who, formatMoney(askerCents), formatMoney(lineTotal))}
        </p>
      ) : null}
      <div className="kt-two-up kt-ask-card__actions">
        <PillButton
          variant="secondary"
          size={52}
          loading={busy === "no"}
          disabled={busy === "yes"}
          onClick={() => void answer("decline")}
        >
          {strings.requests.decline}
        </PillButton>
        <PillButton
          size={52}
          loading={busy === "yes"}
          disabled={busy === "no" || (paying && (invalid || !ready))}
          onClick={() => void answer(choice)}
        >
          {strings.requests.yes}
        </PillButton>
      </div>
    </>
  );
}

/** FR-81: Equal share (or the suggestion) · 25% · 50% · 100% · Custom (a percent or a value). */
function PartPicker({
  decision,
  part,
  unit,
  raw,
  lineTotal,
  invalid,
  onPart,
  onUnit,
  onRaw,
}: {
  decision: "split" | "chip_in";
  part: PartChoice;
  unit: PartUnit;
  raw: string;
  lineTotal: number;
  invalid: boolean;
  onPart: (part: PartChoice) => void;
  onUnit: (unit: PartUnit) => void;
  onRaw: (raw: string) => void;
}) {
  const groupId = useId();
  const inputId = useId();
  const errorId = useId();
  const choices: { value: PartChoice; label: string }[] = [
    {
      value: "base",
      label: decision === "split" ? strings.requests.partEqual : strings.requests.partSuggested,
    },
    ...PART_PRESETS.map((n) => ({ value: n, label: strings.requests.partPercent(n) })),
    { value: "custom", label: strings.requests.partCustom },
  ];
  const inputLabel =
    unit === "percent"
      ? strings.requests.partPercentLabel
      : decision === "chip_in"
        ? strings.requests.chipInLabel
        : strings.requests.partDollarsLabel;
  return (
    <div className="kt-part">
      <span id={groupId} className="kt-field__label">
        {strings.requests.partLabel}
      </span>
      <div className="kt-part__chips" role="radiogroup" aria-labelledby={groupId}>
        {choices.map((c) => (
          <button
            key={String(c.value)}
            type="button"
            role="radio"
            aria-checked={part === c.value}
            className="kt-part__chip"
            onClick={() => onPart(c.value)}
          >
            {c.label}
          </button>
        ))}
      </div>
      {part === "custom" ? (
        <div className="kt-part__custom">
          <SegmentedControl<PartUnit>
            label={strings.requests.partUnitLabel}
            value={unit}
            onChange={onUnit}
            options={[
              { value: "percent", label: strings.requests.partUnitPercent },
              { value: "dollars", label: strings.requests.partUnitDollars },
            ]}
          />
          <div className="kt-field">
            <label htmlFor={inputId} className="kt-field__label">
              {inputLabel}
            </label>
            <input
              id={inputId}
              className="kt-input kt-input--big kt-input--mono"
              inputMode={unit === "percent" ? "numeric" : "decimal"}
              value={raw}
              aria-invalid={invalid || undefined}
              aria-describedby={invalid ? errorId : undefined}
              onChange={(e) => onRaw(e.target.value)}
            />
            {invalid ? (
              <p id={errorId} className="kt-field-error">
                {unit === "percent"
                  ? strings.requests.partPercentError
                  : strings.requests.chipInError(formatMoney(lineTotal))}
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function RadioCard({
  checked,
  onSelect,
  title,
  children,
}: {
  checked: boolean;
  onSelect: () => void;
  title: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      className="kt-radio-card"
      onClick={onSelect}
    >
      <span className="kt-radio-card__dot" aria-hidden="true" />
      <span className="kt-radio-card__text">
        <span className="kt-radio-card__title">{title}</span>
        <span className="kt-radio-card__note">{children}</span>
      </span>
    </button>
  );
}

function Answered({
  req,
  meMemberId,
  now,
  writes,
  onError,
}: {
  req: ShareRequest;
  meMemberId: string;
  now: Date;
  writes: KitchenWrites;
  onError: (error: unknown) => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="kt-ask-card__done">
      <StampText stamp={requestStamp(req, now)} />
      <span className="kt-ask-card__note">{doneNote(req, meMemberId)}</span>
      {canUndo(req, now) ? (
        <button
          type="button"
          className="kt-text-button"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            writes
              .undo(req)
              .catch(onError)
              .finally(() => setBusy(false));
          }}
        >
          {strings.requests.undo}
        </button>
      ) : null}
    </div>
  );
}

function OutgoingCard({
  req,
  now,
  writes,
  onError,
}: {
  req: ShareRequest;
  now: Date;
  writes: KitchenWrites;
  onError: (error: unknown) => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <article className="kt-card kt-out-card" data-status={req.status}>
      <ItemBubble req={req} />
      <div className="kt-out-card__text">
        <p className="kt-out-card__line">{outgoingText(req)}</p>
        <StampText stamp={requestStamp(req, now)} />
      </div>
      {req.status === "pending" ? (
        <PillButton
          variant="secondary"
          size={44}
          loading={busy}
          onClick={() => {
            setBusy(true);
            writes
              .cancel(req.id)
              .catch(onError)
              .finally(() => setBusy(false));
          }}
        >
          {strings.requests.cancel}
        </PillButton>
      ) : null}
    </article>
  );
}
