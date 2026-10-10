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
import { useRequests } from "@/lib/api/kitchen-hooks";
import type { AnswerOption, ShareRequest } from "@/lib/api/kitchen-types";
import { centsToInput, colorIndexOf } from "@/lib/bills";
import { formatMoney } from "@/lib/format";
import {
  ageLabel,
  canUndo,
  chipInCents,
  coOwnersLine,
  doneNote,
  itemWords,
  outgoingText,
  requestStamp,
  splitDeltaChips,
  type Stamp,
} from "@/lib/kitchen";
import { useKitchenWrites, type KitchenWrites } from "@/lib/kitchen-writes";
import { strings } from "@/lib/strings";
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

function ItemBubble({ req, size = 44 }: { req: ShareRequest; size?: number }) {
  return (
    <span
      className="kt-food kt-food--bubble"
      style={{ background: foodPastel(req.item.icon_key), width: size, height: size }}
    >
      <FoodIcon iconKey={req.item.icon_key} size={28} strokeWidth={1.5} />
    </span>
  );
}

function IncomingCard({
  req,
  meMemberId,
  names,
  now,
  writes,
  onError,
}: {
  req: ShareRequest;
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
  meMemberId,
  names,
  writes,
  onError,
}: {
  req: ShareRequest;
  meMemberId: string;
  names: Record<string, string>;
  writes: KitchenWrites;
  onError: (error: unknown) => void;
}) {
  const labelId = useId();
  const chipId = useId();
  const chipErrorId = useId();
  const options: AnswerOption[] = req.options ?? ["free"];
  const [choice, setChoice] = useState<AnswerOption>("free");
  const [chip, setChip] = useState(centsToInput(req.chip_in_suggestion_cents ?? 0));
  const [busy, setBusy] = useState<"yes" | "no" | null>(null);
  const [key, setKey] = useState(newId);
  const lineTotal = req.line_total_cents ?? 0;
  const chipCents = chipInCents(chip, lineTotal);
  const chipInvalid = choice === "chip_in" && chipCents === null;
  const who = req.requester.display_name ?? strings.requests.someone;
  const store = req.source?.store_name ?? strings.bill.untitled;

  async function answer(decision: "decline" | AnswerOption) {
    if (decision === "chip_in" && chipCents === null) return;
    setBusy(decision === "decline" ? "no" : "yes");
    try {
      await writes.answer(
        req,
        decision,
        decision === "chip_in" ? chipCents : null,
        `${key}:${decision}`,
      );
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
              {splitDeltaChips(req.split_deltas ?? {}, names, meMemberId).map((d) => (
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
      {choice === "chip_in" ? (
        <div className="kt-field">
          <label htmlFor={chipId} className="kt-field__label">
            {strings.requests.chipInLabel}
          </label>
          <input
            id={chipId}
            className="kt-input kt-input--big kt-input--mono"
            inputMode="decimal"
            value={chip}
            aria-invalid={chipInvalid || undefined}
            aria-describedby={chipInvalid ? chipErrorId : undefined}
            onChange={(e) => setChip(e.target.value)}
          />
          {chipInvalid ? (
            <p id={chipErrorId} className="kt-field-error">
              {strings.requests.chipInError(formatMoney(lineTotal))}
            </p>
          ) : null}
        </div>
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
          disabled={busy === "no" || chipInvalid}
          onClick={() => void answer(choice)}
        >
          {strings.requests.yes}
        </PillButton>
      </div>
    </>
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
