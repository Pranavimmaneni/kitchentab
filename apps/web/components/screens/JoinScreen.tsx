"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { MemberAvatar } from "@/components/MemberAvatar";
import { PillButton } from "@/components/PillButton";
import { ReceiptCard } from "@/components/ReceiptCard";
import { ThemeToggle } from "@/components/ThemeToggle";
import { api, isApiError, newId } from "@/lib/api/client";
import { keys } from "@/lib/api/hooks";
import type { AuthMethods, Channel, ClaimOut, Member } from "@/lib/api/types";
import { actionKey, clearActionKey } from "@/lib/auth-flow";
import { colorIndexOf } from "@/lib/bills";
import { strings } from "@/lib/strings";
import { CodeRequestForm } from "./CodeRequestForm";
import { GoogleButton } from "./GoogleButton";

export type JoinPreview =
  | { kind: "bill"; store: string; totalText: string; meta: string }
  | { kind: "invite"; house: string; meta: string };

export type JoinScreenProps = {
  kind: "bill" | "invite";
  token: string;
  headline: string;
  preview: JoinPreview;
  placeholders: Member[];
  signedIn: boolean;
  /** Admin (invite) or payer (bill) to ask when your name is missing. */
  askName: string | null;
  /** Where a finished claim lands: the bill for bill links, `/` for invites. */
  doneHref: string;
  methods: AuthMethods;
};

type Choice = { memberId: string; name: string } | { memberId: null; name: string };

const claimStorageKey = (token: string) => `kt-pending-claim:${token}`;

function loadClaim(token: string): Choice | null {
  try {
    const raw = sessionStorage.getItem(claimStorageKey(token));
    return raw ? (JSON.parse(raw) as Choice) : null;
  } catch {
    return null;
  }
}

function saveClaim(token: string, pick: Choice): void {
  try {
    sessionStorage.setItem(claimStorageKey(token), JSON.stringify(pick));
  } catch {
    // ignore
  }
}

function clearClaim(token: string): void {
  try {
    sessionStorage.removeItem(claimStorageKey(token));
  } catch {
    // ignore
  }
  clearActionKey(`claim:${token}`);
}

/**
 * D · Join from a link (PRD §14A, §7 "Placeholders and claiming"): read-only preview, "Which one
 * are you?", then sign in (signed out) or "Join as {name}" (signed in). After verifying, B
 * returns here and the pending claim finishes automatically.
 */
export function JoinScreen({
  kind,
  token,
  headline,
  preview,
  placeholders,
  signedIn,
  askName,
  doneHref,
  methods,
}: JoinScreenProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const newNameId = useId();
  const [pick, setPick] = useState<Choice | null>(null);
  const [newMode, setNewMode] = useState(false);
  const [newName, setNewName] = useState("");
  const [channel, setChannel] = useState<Channel | null>(null);
  const [claiming, setClaiming] = useState(false);
  const [claimError, setClaimError] = useState<"taken" | string | null>(null);
  const autoClaimed = useRef(false);
  const basePath = `/${kind === "bill" ? "b" : "h"}/${token}`;

  async function claim(target: Choice): Promise<void> {
    setClaiming(true);
    setClaimError(null);
    const path = kind === "bill" ? `/b/${token}/claim` : `/join/${token}/claim`;
    const body = target.memberId ? { member_id: target.memberId } : { new_name: target.name };
    try {
      await api.post<ClaimOut>(path, body, { idempotencyKey: actionKey(`claim:${token}`, newId) });
      clearClaim(token);
      // The cached `me` predates the claim (no houses yet): drop it so the next screen refetches.
      queryClient.removeQueries({ queryKey: keys.me });
      router.replace(doneHref);
    } catch (error) {
      clearClaim(token);
      setClaiming(false);
      if (isApiError(error) && error.code === "ALREADY_MEMBER") {
        router.replace(doneHref);
        return;
      }
      if (isApiError(error) && error.code === "ALREADY_CLAIMED") {
        setClaimError("taken");
        setPick(null);
        router.refresh();
        return;
      }
      setClaimError(isApiError(error) ? error.title : strings.error.title);
    }
  }

  // Coming back from B signed in: finish the claim picked before verifying.
  useEffect(() => {
    if (!signedIn || autoClaimed.current) return;
    const pending = loadClaim(token);
    if (!pending) return;
    autoClaimed.current = true;
    setPick(pending);
    void claim(pending);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn, token]);

  function choose(next: Choice | null, isNew = false): void {
    setPick(next);
    setNewMode(isNew);
    setChannel(null);
    setClaimError(null);
  }

  const chosen: Choice | null = newMode
    ? newName.trim()
      ? { memberId: null, name: newName.trim() }
      : null
    : pick;

  function rememberAndVerify(next: Channel): void {
    if (!chosen) return;
    saveClaim(token, chosen);
    setChannel(next);
  }

  return (
    <div className="kt-screen">
      <div className="kt-topbar">
        <span className="kt-kicker">{strings.join.topRow}</span>
        <ThemeToggle />
      </div>
      <main className="kt-screen__main">
        <h1 className="kt-join__headline">{headline}</h1>
        <ReceiptCard heading={preview.kind === "bill" ? preview.store : preview.house} hideHeading>
          <div className="kt-preview">
            <div className="kt-preview__top" aria-hidden="true">
              <span>{preview.kind === "bill" ? preview.store : preview.house}</span>
              {preview.kind === "bill" ? <span className="kt-money">{preview.totalText}</span> : null}
            </div>
            {preview.kind === "bill" ? (
              <span className="sr-only">{preview.totalText}</span>
            ) : null}
            <p className="kt-preview__meta">{preview.meta}</p>
            {preview.kind === "bill" ? (
              <>
                <hr className="kt-rule" />
                <p className="kt-preview__peek">{strings.join.peek}</p>
              </>
            ) : null}
          </div>
        </ReceiptCard>

        {claimError === "taken" ? (
          <div className="kt-notice" role="alert">
            <p className="kt-notice__title">{strings.join.alreadyClaimed}</p>
            <p className="kt-notice__body">{strings.join.askAdmin}</p>
          </div>
        ) : claimError ? (
          <p className="kt-field-error" role="alert">
            {claimError}
          </p>
        ) : null}

        <h2 className="kt-section-label">{strings.join.whichOne}</h2>
        <ul className="kt-picker">
          {placeholders.map((m) => (
            <li key={m.id}>
              <button
                type="button"
                className="kt-pick"
                aria-pressed={!newMode && pick?.memberId === m.id}
                onClick={() => choose({ memberId: m.id, name: m.display_name })}
              >
                <MemberAvatar name={m.display_name} initial={m.initial} colorIndex={colorIndexOf(m)} size={32} />
                {m.display_name}
              </button>
            </li>
          ))}
          <li>
            <button
              type="button"
              className="kt-pick"
              data-new=""
              aria-pressed={newMode}
              onClick={() => choose(null, true)}
            >
              {strings.join.imNew}
            </button>
          </li>
        </ul>

        {newMode ? (
          <div className="kt-field">
            <label htmlFor={newNameId} className="kt-field__label">
              {strings.join.yourName}
            </label>
            <input
              id={newNameId}
              className="kt-input kt-input--big"
              value={newName}
              maxLength={24}
              autoComplete="given-name"
              autoFocus
              onChange={(event) => {
                setNewName(event.target.value);
                setChannel(null);
              }}
            />
          </div>
        ) : null}

        {chosen && signedIn ? (
          <PillButton fullWidth loading={claiming} onClick={() => void claim(chosen)}>
            {claiming ? strings.join.joining : strings.join.joinAs(chosen.name)}
          </PillButton>
        ) : null}

        {chosen && !signedIn ? (
          <>
            <p className="kt-quick-check">{strings.join.quickCheck(chosen.name)}</p>
            {methods.google ? (
              <GoogleButton returnTo={basePath} onClick={() => saveClaim(token, chosen)} />
            ) : null}
            <div className="kt-two-up">
              <PillButton
                variant="secondary"
                size={52}
                pressed={channel === "email"}
                onClick={() => rememberAndVerify("email")}
              >
                {strings.join.emailCode}
              </PillButton>
              {methods.phone ? (
                <PillButton
                  variant="secondary"
                  size={52}
                  pressed={channel === "phone"}
                  onClick={() => rememberAndVerify("phone")}
                >
                  {strings.join.phoneCode}
                </PillButton>
              ) : null}
            </div>
            {channel ? (
              <CodeRequestForm
                key={channel}
                phoneEnabled={methods.phone}
                channel={channel}
                returnTo={basePath}
                autoFocus
              />
            ) : null}
          </>
        ) : null}

        <p className="kt-footnote">{strings.join.notOnList(askName ?? strings.join.theAdmin)}</p>
      </main>
    </div>
  );
}
