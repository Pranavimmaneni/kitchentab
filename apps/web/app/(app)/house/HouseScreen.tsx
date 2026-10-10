"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useId, useState, type FormEvent } from "react";
import { BottomNav } from "@/components/BottomNav";
import { BottomSheet } from "@/components/BottomSheet";
import { Dialog } from "@/components/Dialog";
import { MemberAvatar } from "@/components/MemberAvatar";
import { PillButton } from "@/components/PillButton";
import { ReceiptCard } from "@/components/ReceiptCard";
import { Skeleton } from "@/components/Skeleton";
import { Toast } from "@/components/Toast";
import { MoreIcon, PlusIcon } from "@/components/icons";
import { ErrorCard } from "@/components/screens/ErrorCard";
import { HouseGate, type HouseContext } from "@/components/screens/HouseGate";
import { HouseHeader } from "@/components/screens/HouseHeader";
import { api, isApiError, newId } from "@/lib/api/client";
import { keys, useHouse } from "@/lib/api/hooks";
import { useKitchenHistory } from "@/lib/api/kitchen-hooks";
import type { HouseView, Member } from "@/lib/api/types";
import { colorIndexOf } from "@/lib/bills";
import { forgetHouse } from "@/lib/house";
import { monDayUpper } from "@/lib/kitchen";
import { copyText, displayLink, shareOrCopy } from "@/lib/share";
import { strings } from "@/lib/strings";

/** House settings (PRD §14A): name, members, invite link, admin actions, leave (FR-02..FR-09). */
export function HouseScreen() {
  return <HouseGate title={strings.nav.house}>{(ctx) => <House {...ctx} />}</HouseGate>;
}

type Confirm =
  { kind: "remove" | "admin" | "unclaim"; member: Member } | { kind: "rotate" } | { kind: "leave" };

function House({ house, houses, select }: HouseContext) {
  const view = useHouse(house.id);
  const [menuFor, setMenuFor] = useState<Member | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [toast, setToast] = useState<{ message: string; variant?: "info" | "error" } | null>(null);
  const data = view.data;
  const isAdmin = data?.me.role === "admin";
  const visible = (data?.members ?? []).filter((m) => m.status !== "removed");
  const memberCount = visible.filter(
    (m) => m.status === "active" || m.status === "placeholder",
  ).length;

  const notify = (message: string) => setToast({ message });
  const fail = (error: unknown) =>
    setToast({ message: isApiError(error) ? error.title : strings.error.title, variant: "error" });

  return (
    <div className="kt-screen kt-house">
      <HouseHeader
        house={house}
        houses={houses}
        onSelect={select}
        title={data?.house.name ?? house.name}
      />
      <main className="kt-screen__main">
        {view.isError ? <ErrorCard error={view.error} onRetry={() => void view.refetch()} /> : null}
        {!data && !view.isError ? <Skeleton variant="card" lines={5} /> : null}
        {data ? (
          <>
            <p className="kt-kicker">{strings.houseScreen.step(memberCount)}</p>
            {isAdmin ? <RenameHouse view={data} onDone={notify} onError={fail} /> : null}
            <section className="kt-field" aria-labelledby="members-heading">
              <h2 id="members-heading" className="kt-field__label">
                {strings.houseScreen.members}
              </h2>
              <ul className="kt-list kt-members">
                {visible.map((m) => (
                  <li key={m.id} className="kt-member-row" data-status={m.status}>
                    <MemberAvatar
                      name={m.display_name}
                      colorIndex={colorIndexOf(m)}
                      size={32}
                      placeholder={m.status === "placeholder"}
                    />
                    <span className="kt-member-row__name">{m.display_name}</span>
                    <span className="kt-member-row__chips">
                      {m.id === data.me.member_id ? (
                        <span className="kt-tag">{strings.houseScreen.you}</span>
                      ) : null}
                      {m.role === "admin" ? (
                        <span className="kt-tag" data-tone="lime">
                          {strings.houseScreen.admin}
                        </span>
                      ) : null}
                      <span className="kt-tag" data-tone="muted">
                        {strings.houseScreen.status[m.status]}
                      </span>
                    </span>
                    {isAdmin && m.id !== data.me.member_id && m.status !== "left" ? (
                      <button
                        type="button"
                        className="kt-icon-button"
                        aria-label={strings.houseScreen.memberMenu(m.display_name)}
                        onClick={() => setMenuFor(m)}
                      >
                        <MoreIcon size={20} />
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
              {isAdmin ? <AddMember houseId={house.id} onError={fail} /> : null}
            </section>
            <InviteCard
              view={data}
              isAdmin={isAdmin}
              onRotate={() => setConfirm({ kind: "rotate" })}
              onToast={notify}
            />
            <KitchenHistory houseId={house.id} />
            <PillButton
              variant="danger"
              size={52}
              fullWidth
              onClick={() => setConfirm({ kind: "leave" })}
            >
              {strings.houseScreen.leave}
            </PillButton>
          </>
        ) : null}
      </main>
      <BottomNav />
      {data ? (
        <>
          <MemberSheet
            view={data}
            member={menuFor}
            onClose={() => setMenuFor(null)}
            onPick={(kind, member) => {
              setMenuFor(null);
              setConfirm({ kind, member });
            }}
            onRenamed={(name) => {
              setMenuFor(null);
              notify(name);
            }}
            onError={fail}
          />
          <ConfirmDialog
            view={data}
            confirm={confirm}
            onClose={() => setConfirm(null)}
            onDone={(message) => {
              setConfirm(null);
              if (message) notify(message);
            }}
            onLeft={() => {
              forgetHouse();
            }}
          />
        </>
      ) : null}
      <Toast
        message={toast?.message ?? null}
        variant={toast?.variant}
        onDismiss={() => setToast(null)}
      />
    </div>
  );
}

function RenameHouse({
  view,
  onDone,
  onError,
}: {
  view: HouseView;
  onDone: (message: string) => void;
  onError: (error: unknown) => void;
}) {
  const client = useQueryClient();
  const id = useId();
  const [name, setName] = useState(view.house.name);
  const [saving, setSaving] = useState(false);
  const changed = name.trim() !== view.house.name && name.trim().length > 0;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!changed) return;
    setSaving(true);
    try {
      await api.patch(`/houses/${view.house.id}`, { name: name.trim() }, view.house.version);
      onDone(strings.houseScreen.renamed);
    } catch (error) {
      onError(error);
    } finally {
      setSaving(false);
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.house(view.house.id) }),
        client.invalidateQueries({ queryKey: keys.me }),
      ]);
    }
  }

  return (
    <form className="kt-field" onSubmit={(e) => void submit(e)}>
      <label htmlFor={id} className="kt-field__label">
        {strings.houseScreen.name}
      </label>
      <div className="kt-inline-form">
        <input
          id={id}
          className="kt-input kt-input--big kt-input--mono"
          value={name}
          maxLength={40}
          onChange={(e) => setName(e.target.value)}
        />
        <PillButton
          type="submit"
          variant="secondary"
          size={52}
          disabled={!changed}
          loading={saving}
        >
          {strings.houseScreen.save}
        </PillButton>
      </div>
    </form>
  );
}

function AddMember({ houseId, onError }: { houseId: string; onError: (error: unknown) => void }) {
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setSaving(true);
    try {
      await api.post(`/houses/${houseId}/members`, { name: trimmed }, { idempotencyKey: newId() });
      setName("");
    } catch (error) {
      onError(error);
    } finally {
      setSaving(false);
      await client.invalidateQueries({ queryKey: keys.house(houseId) });
    }
  }
  return (
    <form className="kt-add-row" onSubmit={(e) => void submit(e)}>
      <input
        className="kt-input"
        aria-label={strings.houseScreen.addMember}
        placeholder={strings.houseScreen.addPlaceholder}
        value={name}
        maxLength={24}
        onChange={(e) => setName(e.target.value)}
      />
      <button
        type="submit"
        className="kt-add-button"
        aria-label={strings.houseScreen.addMember}
        disabled={saving}
      >
        <PlusIcon size={22} />
      </button>
    </form>
  );
}

function InviteCard({
  view,
  isAdmin,
  onRotate,
  onToast,
}: {
  view: HouseView;
  isAdmin: boolean;
  onRotate: () => void;
  onToast: (message: string) => void;
}) {
  const url =
    view.invite_token && typeof window !== "undefined"
      ? `${window.location.origin}/h/${view.invite_token}`
      : "";
  return (
    <ReceiptCard heading={strings.houseScreen.invite}>
      <div className="kt-invite">
        <div className="kt-invite__link">
          <span className="kt-invite__url" data-testid="invite-link">
            {url ? displayLink(url) : "…"}
          </span>
          <button
            type="button"
            className="kt-copy-button"
            disabled={!url}
            onClick={() => void copyText(url).then((ok) => ok && onToast(strings.setup.copied))}
          >
            {strings.setup.copy}
          </button>
        </div>
        <div className="kt-two-up">
          <PillButton
            size={52}
            disabled={!url}
            onClick={() =>
              void shareOrCopy({
                title: view.house.name,
                text: strings.setup.inviteText(view.house.name),
                url,
              }).then((r) => r === "copied" && onToast(strings.setup.copied))
            }
          >
            {strings.houseScreen.share}
          </PillButton>
          {isAdmin ? (
            <PillButton variant="dashed" size={52} onClick={onRotate}>
              {strings.houseScreen.rotate}
            </PillButton>
          ) : null}
        </div>
      </div>
    </ReceiptCard>
  );
}

function MemberSheet({
  view,
  member,
  onClose,
  onPick,
  onRenamed,
  onError,
}: {
  view: HouseView;
  member: Member | null;
  onClose: () => void;
  onPick: (kind: "remove" | "admin" | "unclaim", member: Member) => void;
  onRenamed: (message: string) => void;
  onError: (error: unknown) => void;
}) {
  const client = useQueryClient();
  const id = useId();
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [saving, setSaving] = useState(false);

  async function rename(event: FormEvent) {
    event.preventDefault();
    if (!member || !name.trim()) return;
    setSaving(true);
    try {
      await api.patch(
        `/houses/${view.house.id}/members/${member.id}`,
        { name: name.trim() },
        member.version,
      );
      setRenaming(false);
      onRenamed(strings.item.saved);
    } catch (error) {
      onError(error);
    } finally {
      setSaving(false);
      await client.invalidateQueries({ queryKey: keys.house(view.house.id) });
    }
  }

  return (
    <BottomSheet
      open={member !== null}
      onClose={() => {
        setRenaming(false);
        onClose();
      }}
      title={member?.display_name ?? ""}
    >
      {member ? (
        renaming ? (
          <form className="kt-form" onSubmit={(e) => void rename(e)}>
            <label htmlFor={id} className="kt-field__label">
              {strings.houseScreen.renameTitle(member.display_name)}
            </label>
            <input
              id={id}
              className="kt-input kt-input--big"
              value={name}
              maxLength={24}
              onChange={(e) => setName(e.target.value)}
            />
            <PillButton type="submit" size={52} fullWidth loading={saving} disabled={!name.trim()}>
              {strings.houseScreen.save}
            </PillButton>
          </form>
        ) : (
          <div className="kt-form">
            <PillButton
              variant="secondary"
              size={52}
              fullWidth
              onClick={() => {
                setName(member.display_name);
                setRenaming(true);
              }}
            >
              {strings.houseScreen.rename}
            </PillButton>
            {member.claimed ? (
              <PillButton
                variant="secondary"
                size={52}
                fullWidth
                onClick={() => onPick("unclaim", member)}
              >
                {strings.houseScreen.unclaim}
              </PillButton>
            ) : null}
            {member.status === "active" ? (
              <PillButton
                variant="secondary"
                size={52}
                fullWidth
                onClick={() => onPick("admin", member)}
              >
                {strings.houseScreen.makeAdmin}
              </PillButton>
            ) : null}
            <PillButton
              variant="danger"
              size={52}
              fullWidth
              onClick={() => onPick("remove", member)}
            >
              {strings.houseScreen.remove}
            </PillButton>
          </div>
        )
      ) : null}
    </BottomSheet>
  );
}

function ConfirmDialog({
  view,
  confirm,
  onClose,
  onDone,
  onLeft,
}: {
  view: HouseView;
  confirm: Confirm | null;
  onClose: () => void;
  onDone: (message?: string) => void;
  onLeft: () => void;
}) {
  const client = useQueryClient();
  const router = useRouter();
  const errorId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hid = view.house.id;

  if (!confirm) return null;
  const copy =
    confirm.kind === "remove"
      ? {
          title: strings.houseScreen.removeTitle(confirm.member.display_name),
          body: strings.houseScreen.removeBody,
          label: strings.houseScreen.removeConfirm,
          danger: true,
        }
      : confirm.kind === "admin"
        ? {
            title: strings.houseScreen.adminTitle(confirm.member.display_name),
            body: strings.houseScreen.adminBody,
            label: strings.houseScreen.adminConfirm,
            danger: false,
          }
        : confirm.kind === "unclaim"
          ? {
              title: strings.houseScreen.unclaimTitle(confirm.member.display_name),
              body: strings.houseScreen.unclaimBody,
              label: strings.houseScreen.unclaimConfirm,
              danger: true,
            }
          : confirm.kind === "rotate"
            ? {
                title: strings.houseScreen.rotateTitle,
                body: strings.houseScreen.rotateBody,
                label: strings.houseScreen.rotateConfirm,
                danger: true,
              }
            : {
                title: strings.houseScreen.leaveTitle(view.house.name),
                body: strings.houseScreen.leaveBody,
                label: strings.houseScreen.leaveConfirm,
                danger: true,
              };

  async function run() {
    if (!confirm) return;
    setBusy(true);
    setError(null);
    try {
      if (confirm.kind === "remove") await api.del(`/houses/${hid}/members/${confirm.member.id}`);
      else if (confirm.kind === "admin")
        await api.post(`/houses/${hid}/members/${confirm.member.id}/make-admin`, undefined, {
          idempotencyKey: newId(),
        });
      else if (confirm.kind === "unclaim")
        await api.post(`/houses/${hid}/members/${confirm.member.id}/unclaim`, undefined, {
          idempotencyKey: newId(),
        });
      else if (confirm.kind === "rotate")
        await api.post(`/houses/${hid}/invite/rotate`, undefined, { idempotencyKey: newId() });
      else {
        await api.post(`/houses/${hid}/leave`, undefined, { idempotencyKey: newId() });
        onLeft();
        await client.invalidateQueries({ queryKey: keys.me });
        router.replace("/");
        onDone();
        return;
      }
      await client.invalidateQueries({ queryKey: ["house", hid] });
      onDone(confirm.kind === "rotate" ? strings.houseScreen.rotated : undefined);
    } catch (err) {
      setError(isApiError(err) ? err.title : strings.error.title);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open
      onClose={() => {
        setError(null);
        onClose();
      }}
      title={copy.title}
      variant={copy.danger ? "danger" : "confirm"}
      confirmLabel={copy.label}
      onConfirm={() => void run()}
      confirmLoading={busy}
    >
      <p>{copy.body}</p>
      {error ? (
        <p id={errorId} className="kt-field-error" role="alert">
          {error}
        </p>
      ) : null}
    </Dialog>
  );
}

function KitchenHistory({ houseId }: { houseId: string }) {
  const history = useKitchenHistory(houseId);
  const data = history.data;
  if (!data) return null;
  return (
    <section className="kt-field" aria-labelledby="kitchen-history">
      <h2 id="kitchen-history" className="kt-field__label">
        {strings.houseScreen.history}
      </h2>
      {data.used_up.length === 0 ? (
        <p className="kt-hint">{strings.houseScreen.noHistory}</p>
      ) : null}
      <ul className="kt-list kt-history">
        {data.used_up.map((i) => (
          <li key={i.id} className="kt-history__row">
            {strings.houseScreen.usedUp(
              i.name,
              i.used_up_at ? monDayUpper(i.used_up_at.slice(0, 10)) : "",
            )}
          </li>
        ))}
      </ul>
      {data.answered.length > 0 ? (
        <p className="kt-hint">{strings.houseScreen.answered(data.answered.length)}</p>
      ) : null}
    </section>
  );
}
