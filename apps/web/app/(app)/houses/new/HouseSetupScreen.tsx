"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { AppHeader } from "@/components/AppHeader";
import { MemberAvatar } from "@/components/MemberAvatar";
import { PillButton } from "@/components/PillButton";
import { ReceiptCard } from "@/components/ReceiptCard";
import { PlusIcon, XIcon } from "@/components/icons";
import { ActionBar } from "@/components/screens/ActionBar";
import { ErrorCard } from "@/components/screens/ErrorCard";
import { ScreenToast } from "@/components/screens/ScreenToast";
import { useToast } from "@/components/screens/useToast";
import { api, isApiError, newId } from "@/lib/api/client";
import { keys, useHouse } from "@/lib/api/hooks";
import type { CreatedHouse, HouseView, Member } from "@/lib/api/types";
import { actionKey, clearActionKey } from "@/lib/auth-flow";
import { colorIndexOf } from "@/lib/bills";
import { copyText, displayLink, shareOrCopy } from "@/lib/share";
import { strings } from "@/lib/strings";
import { writeThrough } from "@/lib/sync/write-cache";

const HOUSE_KEY = "kt-setup-house";
const MAX_ROOMMATES = 19;

function storedHouse(): string | null {
  try {
    return sessionStorage.getItem(HOUSE_KEY);
  } catch {
    return null;
  }
}

function forgetSetup(): void {
  try {
    sessionStorage.removeItem(HOUSE_KEY);
  } catch {
    // ignore
  }
  clearActionKey("create-house");
}

/**
 * C · Who lives here (PRD §14A). The house is created as soon as the screen opens (named
 * "Our place") so the invite link can be shown and shared right away, as in House.dc.html;
 * the name and roommates then save as you edit them.
 */
export function HouseSetupScreen() {
  const queryClient = useQueryClient();
  const nameId = useId();
  const addId = useId();
  const addErrorId = useId();
  const [houseId, setHouseId] = useState<string | null>(null);
  const [createError, setCreateError] = useState<unknown>(null);
  const [name, setName] = useState<string>(strings.setup.defaultName);
  const [draft, setDraft] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [copied, setCopied] = useState(false);
  const { toast, show, dismiss } = useToast();
  const house = useHouse(houseId);
  const refetchHouse = house.refetch;
  const nameTouched = useRef(false);

  // Create (or resume) the house.
  useEffect(() => {
    const existing = storedHouse();
    if (existing) {
      setHouseId(existing);
      return;
    }
    let cancelled = false;
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "America/New_York";
    api
      .post<CreatedHouse>(
        "/houses",
        { name: strings.setup.defaultName, roommates: [], timezone },
        { idempotencyKey: actionKey("create-house", newId) },
      )
      .then((created) => {
        if (cancelled) return;
        try {
          sessionStorage.setItem(HOUSE_KEY, created.house.id);
        } catch {
          // ignore
        }
        queryClient.setQueryData<HouseView>(keys.house(created.house.id), {
          house: { ...created.house, timezone },
          me: { member_id: created.member_id, role: "admin" },
          members: created.members,
          invite_token: created.invite_token,
        });
        void queryClient.invalidateQueries({ queryKey: keys.me });
        setHouseId(created.house.id);
      })
      .catch((error: unknown) => {
        if (!cancelled) setCreateError(error);
      });
    return () => {
      cancelled = true;
    };
  }, [queryClient]);

  const view = house.data;
  useEffect(() => {
    if (view && !nameTouched.current) setName(view.house.name);
  }, [view]);

  // Autosave the house name (guarded edit, If-Match).
  useEffect(() => {
    if (!view || !nameTouched.current) return;
    const clean = name.trim();
    if (!clean || clean === view.house.name || clean.length > 40) return;
    const timer = window.setTimeout(() => {
      api
        .patch<{ name: string; version: number }>(
          `/houses/${view.house.id}`,
          { name: clean },
          view.house.version,
        )
        .then((out) => {
          queryClient.setQueryData<HouseView>(keys.house(view.house.id), (old) =>
            old ? { ...old, house: { ...old.house, name: out.name, version: out.version } } : old,
          );
        })
        .catch(() => void refetchHouse());
    }, 800);
    return () => window.clearTimeout(timer);
  }, [name, view, queryClient, refetchHouse]);

  const members = (view?.members ?? []).filter((m) => m.status === "active" || m.status === "placeholder");
  const me = members.find((m) => m.id === view?.me.member_id);
  const roommates = members.filter((m) => m.id !== view?.me.member_id);
  const full = roommates.length >= MAX_ROOMMATES;

  function setMembers(update: (list: Member[]) => Member[]): void {
    if (!view) return;
    writeThrough<HouseView>(queryClient, keys.house(view.house.id), (old) => ({
      ...old,
      members: update(old.members),
    }));
  }

  async function addRoommate(raw: string): Promise<boolean> {
    if (!view) return false;
    const clean = raw.trim().replace(/\s+/g, " ");
    if (!clean) return false;
    if (members.some((m) => m.display_name.toLowerCase() === clean.toLowerCase())) {
      setAddError(strings.setup.duplicate);
      return false;
    }
    if (full) {
      setAddError(strings.setup.full);
      return false;
    }
    setAdding(true);
    try {
      const member = await api.post<Member>(`/houses/${view.house.id}/members`, { name: clean.slice(0, 24) });
      // A refetch (live sync, focus) may already have brought the new member in.
      setMembers((list) => (list.some((m) => m.id === member.id) ? list : [...list, member]));
      setAddError(null);
      return true;
    } catch (error) {
      setAddError(
        isApiError(error) && error.code === "DUPLICATE_NAME"
          ? strings.setup.duplicate
          : isApiError(error)
            ? error.title
            : strings.error.title,
      );
      return false;
    } finally {
      setAdding(false);
    }
  }

  async function onAdd(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const sent = draft;
    // Clear only what was sent: the next name may already be half typed when the add returns.
    if (await addRoommate(sent)) setDraft((now) => (now === sent ? "" : now));
  }

  async function remove(member: Member): Promise<void> {
    if (!view) return;
    setMembers((list) => list.filter((m) => m.id !== member.id));
    try {
      await api.del(`/houses/${view.house.id}/members/${member.id}`);
      show({
        message: strings.setup.removed(member.display_name),
        actionLabel: strings.toast.undo,
        onAction: () => void addRoommate(member.display_name),
      });
    } catch (error) {
      void house.refetch();
      show({ message: isApiError(error) ? error.title : strings.error.title, variant: "error" });
    }
  }

  const inviteUrl =
    view?.invite_token && typeof window !== "undefined"
      ? `${window.location.origin}/h/${view.invite_token}`
      : "";

  async function copyInvite(): Promise<void> {
    if (!inviteUrl) return;
    if (await copyText(inviteUrl)) {
      setCopied(true);
      show({ message: strings.setup.copied });
      window.setTimeout(() => setCopied(false), 2000);
    }
  }

  async function shareInvite(): Promise<void> {
    if (!inviteUrl || !view) return;
    const result = await shareOrCopy({ title: view.house.name, text: strings.setup.inviteText(view.house.name), url: inviteUrl });
    if (result === "copied") show({ message: strings.setup.copied });
  }

  if (createError) {
    return (
      <div className="kt-screen">
        <AppHeader stepLabel={strings.setup.step} title={strings.setup.title} />
        <main className="kt-screen__main">
          <ErrorCard error={createError} onRetry={() => window.location.reload()} />
        </main>
      </div>
    );
  }

  return (
    <div className="kt-screen">
      <AppHeader stepLabel={strings.setup.step} title={strings.setup.title} />
      <main className="kt-screen__main" aria-busy={!view || undefined}>
        <div className="kt-field">
          <label htmlFor={nameId} className="kt-field__label">
            {strings.setup.houseName}
          </label>
          <input
            id={nameId}
            className="kt-input kt-input--big kt-input--mono"
            value={name}
            maxLength={40}
            autoComplete="off"
            disabled={!view}
            onChange={(event) => {
              nameTouched.current = true;
              setName(event.target.value);
            }}
          />
        </div>

        <div className="kt-field">
          <span className="kt-field__label" id={`${addId}-label`}>
            {strings.setup.roommates}
          </span>
          <ul className="kt-chips" aria-labelledby={`${addId}-label`}>
            {me ? (
              <li className="kt-chip" data-solo="">
                <MemberAvatar name={me.display_name} initial={me.initial} colorIndex={colorIndexOf(me)} size={28} />
                {strings.setup.you}
              </li>
            ) : null}
            {roommates.map((m) => (
              <li key={m.id} className="kt-chip">
                <MemberAvatar name={m.display_name} initial={m.initial} colorIndex={colorIndexOf(m)} size={28} />
                {m.display_name}
                <button
                  type="button"
                  className="kt-chip__remove"
                  aria-label={strings.setup.remove(m.display_name)}
                  onClick={() => void remove(m)}
                >
                  <XIcon size={18} />
                </button>
              </li>
            ))}
          </ul>
          <form className="kt-add-row" onSubmit={(event) => void onAdd(event)}>
            <label htmlFor={addId} className="sr-only">
              {strings.setup.addLabel}
            </label>
            <input
              id={addId}
              className="kt-input"
              placeholder={strings.setup.addPlaceholder}
              value={draft}
              maxLength={24}
              autoComplete="off"
              enterKeyHint="done"
              disabled={!view}
              aria-invalid={addError ? true : undefined}
              aria-describedby={addError ? addErrorId : undefined}
              onChange={(event) => {
                setDraft(event.target.value);
                if (addError) setAddError(null);
              }}
            />
            <button
              type="submit"
              className="kt-add-button"
              aria-label={strings.setup.addLabel}
              disabled={!view || adding || full}
            >
              <PlusIcon size={22} />
            </button>
          </form>
          {addError ? (
            <p id={addErrorId} className="kt-field-error" role="alert">
              {addError}
            </p>
          ) : null}
        </div>
        <p className="kt-helper">{strings.setup.helper}</p>

        <ReceiptCard heading={strings.setup.inviteHeading}>
          <div className="kt-invite">
            <div className="kt-invite__link">
              <span className="kt-invite__url" data-testid="invite-link">
                {inviteUrl ? displayLink(inviteUrl) : "…"}
              </span>
              <button
                type="button"
                className="kt-copy-button"
                onClick={() => void copyInvite()}
                disabled={!inviteUrl}
              >
                {copied ? strings.setup.copied : strings.setup.copy}
              </button>
            </div>
            <PillButton size={52} fullWidth onClick={() => void shareInvite()} disabled={!inviteUrl}>
              {strings.setup.dropIt}
            </PillButton>
          </div>
        </ReceiptCard>
      </main>
      <ActionBar>
        {view ? (
          <Link
            href={`/bills/new?house=${view.house.id}`}
            className="kt-pill"
            data-variant="ink"
            data-size="56"
            data-full=""
            onClick={forgetSetup}
          >
            {strings.setup.snapFirst}
          </Link>
        ) : (
          <PillButton variant="ink" fullWidth disabled>
            {strings.setup.snapFirst}
          </PillButton>
        )}
        <Link href="/" className="kt-text-link" onClick={forgetSetup}>
          {strings.setup.skip}
        </Link>
      </ActionBar>
      <ScreenToast toast={toast} onDismiss={dismiss} />
    </div>
  );
}
