"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useState, type FormEvent } from "react";
import { AppHeader } from "@/components/AppHeader";
import { Dialog } from "@/components/Dialog";
import { OTPInput } from "@/components/OTPInput";
import { PillButton } from "@/components/PillButton";
import { SegmentedControl } from "@/components/SegmentedControl";
import { Skeleton } from "@/components/Skeleton";
import { Switch } from "@/components/Switch";
import { Toast } from "@/components/Toast";
import { XIcon } from "@/components/icons";
import { ErrorCard } from "@/components/screens/ErrorCard";
import { api, isApiError, newId } from "@/lib/api/client";
import { keys, useAuthMethods, useMe } from "@/lib/api/hooks";
import { tabKeys, useIdentities, usePaymentHandles } from "@/lib/api/tabs-hooks";
import type { Identity, PaymentHandles } from "@/lib/api/tabs-types";
import type { User } from "@/lib/api/types";
import { hapticSupport, hapticsEnabled, setHapticsEnabled } from "@/lib/haptics";
import { forgetHouse, useCurrentHouse } from "@/lib/house";
import { isIos, isStandalone } from "@/lib/pwa/device";
import { promptInstall, usePrompts } from "@/lib/pwa/prompts";
import { strings } from "@/lib/strings";
import { announceSignedOut } from "@/lib/sync/session";
import { setPreference, useTheme, type ThemePreference } from "@/lib/theme";

type Notify = (message: string, variant?: "info" | "error") => void;

function errorText(error: unknown): string {
  return isApiError(error) ? error.title : strings.error.title;
}

/**
 * Settings (PRD §14A, FR-83, FR-84): your name (in every house), notifications, the home screen app,
 * theme, kitchen default, sign-in methods, payment handles, your house, data and account.
 */
export function SettingsScreen() {
  const me = useMe();
  const [toast, setToast] = useState<{ message: string; variant?: "info" | "error" } | null>(null);
  const notify: Notify = (message, variant) => setToast({ message, variant });
  const user = me.data?.user;
  return (
    <div className="kt-screen kt-settings">
      <AppHeader back={{ href: "/tabs" }} title={strings.settings.title} />
      <main className="kt-screen__main">
        {me.isError ? <ErrorCard error={me.error} onRetry={() => void me.refetch()} /> : null}
        {!user && !me.isError ? <Skeleton variant="card" lines={6} /> : null}
        {user ? (
          <>
            <Profile user={user} notify={notify} />
            <Notifications user={user} notify={notify} />
            <HomeScreen notify={notify} />
            <Appearance />
            <Preferences user={user} notify={notify} />
            <SignInMethods notify={notify} />
            <Handles notify={notify} />
            <YourHouse notify={notify} />
            <DataAndAccount notify={notify} />
          </>
        ) : null}
      </main>
      <Toast
        message={toast?.message ?? null}
        variant={toast?.variant}
        onDismiss={() => setToast(null)}
      />
    </div>
  );
}

/** PATCH /me is a set-state write on the user's own row (no If-Match, last write wins). */
async function patchMe(
  body: Partial<Pick<User, "display_name" | "theme" | "share_by_default" | "notify_email">>,
) {
  // A name change also reports the houses it reached and any where the name was taken (FR-83).
  return api.request<User & { name_updated_in?: string[]; name_taken_in?: string[] }>(
    "PATCH",
    "/me",
    { body },
  );
}

function Profile({ user, notify }: { user: User; notify: Notify }) {
  const client = useQueryClient();
  const id = useId();
  const [name, setName] = useState(user.display_name);
  const [saving, setSaving] = useState(false);
  const changed = name.trim().length > 0 && name.trim() !== user.display_name;
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!changed) return;
    setSaving(true);
    try {
      const out = await patchMe({ display_name: name.trim() });
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.me }),
        client.invalidateQueries({ queryKey: ["house"] }),
      ]);
      const taken = out.name_taken_in ?? [];
      notify(
        taken.length > 0
          ? strings.settings.nameSavedTaken(taken.join(", "), out.display_name)
          : strings.settings.saved,
      );
    } catch (error) {
      notify(errorText(error), "error");
    } finally {
      setSaving(false);
    }
  }
  return (
    <form className="kt-field" onSubmit={(e) => void submit(e)}>
      <label htmlFor={id} className="kt-field__label">
        {strings.settings.profile}
      </label>
      <div className="kt-inline-form">
        <input
          id={id}
          className="kt-input kt-input--big"
          value={name}
          maxLength={60}
          autoComplete="name"
          onChange={(e) => setName(e.target.value)}
        />
        <PillButton
          type="submit"
          variant="secondary"
          size={52}
          disabled={!changed}
          loading={saving}
        >
          {strings.settings.saveName}
        </PillButton>
      </div>
    </form>
  );
}

function SignInMethods({ notify }: { notify: Notify }) {
  const client = useQueryClient();
  const identities = useIdentities();
  const methods = useAuthMethods();
  const lastId = useId();
  const [adding, setAdding] = useState<"email" | "phone" | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const list = identities.data ?? [];
  const onlyOne = list.length === 1;

  async function remove(identity: Identity) {
    setRemoving(identity.id);
    try {
      await api.del(`/me/identities/${identity.id}`);
      notify(strings.settings.removed);
    } catch (error) {
      notify(errorText(error), "error");
    } finally {
      setRemoving(null);
      await Promise.all([
        client.invalidateQueries({ queryKey: tabKeys.identities }),
        client.invalidateQueries({ queryKey: keys.me, exact: true }),
      ]);
    }
  }

  return (
    <section className="kt-field" aria-labelledby="methods-heading">
      <h2 id="methods-heading" className="kt-field__label">
        {strings.settings.methods}
      </h2>
      {identities.isPending ? <Skeleton lines={2} /> : null}
      <ul className="kt-list kt-methods">
        {list.map((identity) => (
          <li key={identity.id} className="kt-method-row">
            <span className="kt-method-row__provider">{identity.provider.toUpperCase()}</span>
            <span className="kt-method-row__label">{identity.label}</span>
            <button
              type="button"
              className="kt-icon-button"
              aria-label={strings.settings.remove(identity.label)}
              aria-describedby={onlyOne ? lastId : undefined}
              disabled={onlyOne || removing === identity.id}
              onClick={() => void remove(identity)}
            >
              <XIcon size={18} />
            </button>
          </li>
        ))}
      </ul>
      {onlyOne ? (
        <p id={lastId} className="kt-hint">
          {strings.settings.lastMethod}
        </p>
      ) : null}
      {adding ? (
        <AddMethod
          channel={adding}
          onCancel={() => setAdding(null)}
          onAdded={async () => {
            setAdding(null);
            notify(strings.settings.added);
            await client.invalidateQueries({ queryKey: tabKeys.identities });
            await client.invalidateQueries({ queryKey: keys.me, exact: true });
          }}
        />
      ) : (
        <div className="kt-two-up">
          {methods.data?.email !== false ? (
            <PillButton variant="dashed" size={52} onClick={() => setAdding("email")}>
              {strings.settings.addEmail}
            </PillButton>
          ) : null}
          {methods.data?.phone ? (
            <PillButton variant="dashed" size={52} onClick={() => setAdding("phone")}>
              {strings.settings.addPhone}
            </PillButton>
          ) : null}
        </div>
      )}
    </section>
  );
}

function AddMethod({
  channel,
  onCancel,
  onAdded,
}: {
  channel: "email" | "phone";
  onCancel: () => void;
  onAdded: () => Promise<void>;
}) {
  const id = useId();
  const errorId = useId();
  const [destination, setDestination] = useState("");
  const [masked, setMasked] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function send(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const out = await api.post<{ masked: string }>(
        "/me/identities/start",
        { channel, destination: destination.trim() },
        { retries: 0 },
      );
      setMasked(out.masked);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function verify(value: string) {
    setBusy(true);
    setError(null);
    try {
      await api.post(
        "/me/identities/verify",
        { channel, destination: destination.trim(), code: value },
        { retries: 0 },
      );
      await onAdded();
    } catch (err) {
      setError(errorText(err));
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  if (masked) {
    return (
      <form
        className="kt-form kt-card"
        onSubmit={(e) => {
          e.preventDefault();
          void verify(code);
        }}
      >
        <p className="kt-hint">{strings.settings.codeSent(masked)}</p>
        <OTPInput
          value={code}
          onChange={setCode}
          onComplete={(c) => void verify(c)}
          error={error ?? undefined}
          autoFocus
        />
        <div className="kt-two-up">
          <PillButton variant="secondary" size={52} onClick={onCancel}>
            {strings.settings.cancel}
          </PillButton>
          <PillButton type="submit" size={52} loading={busy} disabled={code.length !== 6}>
            {strings.settings.verify}
          </PillButton>
        </div>
      </form>
    );
  }
  return (
    <form className="kt-form kt-card" onSubmit={(e) => void send(e)}>
      <label htmlFor={id} className="kt-field__label">
        {channel === "email" ? strings.settings.emailLabel : strings.settings.phoneLabel}
      </label>
      <input
        id={id}
        className="kt-input kt-input--big"
        type={channel === "email" ? "email" : "tel"}
        autoComplete={channel === "email" ? "email" : "tel"}
        value={destination}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        onChange={(e) => setDestination(e.target.value)}
      />
      {error ? (
        <p id={errorId} className="kt-field-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="kt-two-up">
        <PillButton variant="secondary" size={52} onClick={onCancel}>
          {strings.settings.cancel}
        </PillButton>
        <PillButton type="submit" size={52} loading={busy} disabled={destination.trim().length < 3}>
          {strings.settings.sendCode}
        </PillButton>
      </div>
    </form>
  );
}

const HANDLE_FIELDS: readonly {
  key: keyof PaymentHandles;
  label: string;
  autoComplete?: string;
}[] = [
  { key: "venmo", label: strings.settings.venmo },
  { key: "cashapp", label: strings.settings.cashapp },
  { key: "paypal", label: strings.settings.paypal },
  { key: "zelle", label: strings.settings.zelle, autoComplete: "email" },
];

function Handles({ notify }: { notify: Notify }) {
  const client = useQueryClient();
  const handles = usePaymentHandles();
  const baseId = useId();
  const [values, setValues] = useState<Record<keyof PaymentHandles, string> | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (handles.data && values === null) {
      setValues({
        venmo: handles.data.venmo ?? "",
        cashapp: handles.data.cashapp ?? "",
        paypal: handles.data.paypal ?? "",
        zelle: handles.data.zelle ?? "",
      });
    }
  }, [handles.data, values]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!values) return;
    setSaving(true);
    try {
      const body = Object.fromEntries(
        Object.entries(values).map(([k, v]) => [k, v.trim() || null]),
      );
      const out = await api.put<PaymentHandles>("/me/payment-handles", body);
      client.setQueryData(tabKeys.handles, out);
      notify(strings.settings.handlesSaved);
    } catch (error) {
      notify(errorText(error), "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="kt-field" aria-labelledby={`${baseId}-h`} onSubmit={(e) => void submit(e)}>
      <h2 id={`${baseId}-h`} className="kt-field__label">
        {strings.settings.handles}
      </h2>
      <p className="kt-hint">{strings.settings.handlesHint}</p>
      {!values ? <Skeleton lines={2} /> : null}
      {values
        ? HANDLE_FIELDS.map((f) => (
            <div key={f.key} className="kt-field">
              <label htmlFor={`${baseId}-${f.key}`} className="kt-field__label">
                {f.label}
              </label>
              <input
                id={`${baseId}-${f.key}`}
                className="kt-input kt-input--big"
                value={values[f.key]}
                maxLength={f.key === "zelle" ? 120 : 60}
                autoComplete={f.autoComplete ?? "off"}
                onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
              />
            </div>
          ))
        : null}
      <PillButton
        type="submit"
        variant="secondary"
        size={52}
        fullWidth
        loading={saving}
        disabled={!values}
      >
        {strings.settings.saveHandles}
      </PillButton>
    </form>
  );
}

function Preferences({ user, notify }: { user: User; notify: Notify }) {
  const client = useQueryClient();
  const shareNote = useId();
  async function set(field: "share_by_default" | "notify_email", value: boolean) {
    client.setQueryData(keys.me, (old: unknown) =>
      old && typeof old === "object" && "user" in old
        ? { ...(old as { user: User }), user: { ...(old as { user: User }).user, [field]: value } }
        : old,
    );
    try {
      await patchMe({ [field]: value });
    } catch (error) {
      notify(errorText(error), "error");
    } finally {
      await client.invalidateQueries({ queryKey: keys.me, exact: true });
    }
  }
  return (
    <>
      <section className="kt-field" aria-labelledby="kitchen-pref">
        <h2 id="kitchen-pref" className="kt-field__label">
          {strings.settings.kitchen}
        </h2>
        <div className="kt-share-row">
          <div className="kt-share-row__text">
            <span className="kt-share-row__title">{strings.settings.shareDefault}</span>
            <span id={shareNote} className="kt-share-row__note">
              {strings.settings.shareDefaultNote}
            </span>
          </div>
          <Switch
            checked={user.share_by_default}
            label={strings.settings.shareDefault}
            describedBy={shareNote}
            onChange={(v) => void set("share_by_default", v)}
          />
        </div>
        <HapticsRow />
      </section>
    </>
  );
}

/** Settings › Kitchen › Haptics (FR-G09): on by default, hidden where the device can't buzz. */
function HapticsRow() {
  const note = useId();
  const [shown, setShown] = useState(false);
  const [on, setOn] = useState(true);
  useEffect(() => {
    setShown(hapticSupport() !== null);
    setOn(hapticsEnabled());
  }, []);
  if (!shown) return null;
  return (
    <div className="kt-share-row" data-testid="haptics-row">
      <div className="kt-share-row__text">
        <span className="kt-share-row__title">{strings.settings.haptics}</span>
        <span id={note} className="kt-share-row__note">
          {strings.settings.hapticsNote}
        </span>
      </div>
      <Switch
        checked={on}
        label={strings.settings.haptics}
        describedBy={note}
        onChange={(v) => {
          setHapticsEnabled(v);
          setOn(v);
        }}
      />
    </div>
  );
}

/** FR-84: notifications in one group: push, email and quiet hours (N2), plus the email switch. */
function Notifications({ user, notify }: { user: User; notify: Notify }) {
  const client = useQueryClient();
  async function setEmail(value: boolean) {
    client.setQueryData(keys.me, (old: unknown) =>
      old && typeof old === "object" && "user" in old
        ? {
            ...(old as { user: User }),
            user: { ...(old as { user: User }).user, notify_email: value },
          }
        : old,
    );
    try {
      await patchMe({ notify_email: value });
    } catch (error) {
      notify(errorText(error), "error");
    } finally {
      await client.invalidateQueries({ queryKey: keys.me, exact: true });
    }
  }
  return (
    <section className="kt-field" aria-labelledby="notify-heading">
      <h2 id="notify-heading" className="kt-field__label">
        {strings.settings.notifications}
      </h2>
      {/* N2 · Settings › Notifications (PRD §20.2) */}
      <Link href="/settings/notifications" className="kt-link-row">
        <span className="kt-link-row__text">
          <span className="kt-link-row__title">{strings.notify.settingsLink}</span>
          <span className="kt-link-row__hint">{strings.notify.settingsLinkHint}</span>
        </span>
        <span className="kt-link-row__chevron" aria-hidden="true">
          ›
        </span>
      </Link>
      <div className="kt-share-row">
        <span className="kt-share-row__title">{strings.settings.notifyEmail}</span>
        <Switch
          checked={user.notify_email}
          label={strings.settings.notifyEmail}
          onChange={(v) => void setEmail(v)}
        />
      </div>
    </section>
  );
}

function InstallSteps({
  title,
  steps,
  testId,
}: {
  title: string;
  steps: readonly string[];
  testId: string;
}) {
  return (
    <div className="kt-install-steps" data-testid={testId}>
      <h3 className="kt-install-steps__title">{title}</h3>
      <ol className="kt-install-steps__list">
        {steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
    </div>
  );
}

/**
 * FR-84: "Add KitchenTab to your home screen". Steps for iPhone (Safari), Android (Chrome) and
 * computers, this device's first; an Install button when the browser offers one (PWA-05). Hidden
 * once KitchenTab runs from the home screen (and while rendering on the server).
 */
function HomeScreen({ notify }: { notify: Notify }) {
  const prompts = usePrompts();
  const headingId = useId();
  const [device, setDevice] = useState<{ standalone: boolean; ios: boolean } | null>(null);
  useEffect(() => setDevice({ standalone: isStandalone(), ios: isIos() }), []);
  if (!device || device.standalone) return null;
  const iphone = (
    <InstallSteps
      key="ios"
      title={strings.settings.iphone}
      steps={strings.settings.iphoneSteps}
      testId="install-iphone"
    />
  );
  const android = (
    <InstallSteps
      key="android"
      title={strings.settings.android}
      steps={strings.settings.androidSteps}
      testId="install-android"
    />
  );
  return (
    <section className="kt-field" aria-labelledby={headingId} data-testid="home-screen">
      <h2 id={headingId} className="kt-field__label">
        {strings.settings.homeScreen}
      </h2>
      <p className="kt-hint">{strings.settings.homeScreenBody}</p>
      {prompts.canInstall ? (
        <PillButton
          size={52}
          fullWidth
          onClick={() =>
            void promptInstall().then((outcome) => {
              if (outcome === "accepted") notify(strings.settings.installed);
            })
          }
        >
          {strings.settings.install}
        </PillButton>
      ) : null}
      {device.ios ? [iphone, android] : [android, iphone]}
      <div className="kt-install-steps">
        <h3 className="kt-install-steps__title">{strings.settings.computer}</h3>
        <p className="kt-install-steps__line">{strings.settings.computerStep}</p>
      </div>
    </section>
  );
}

/** FR-84: the current house's settings and Leave house, without hunting for the House tab. */
function YourHouse({ notify }: { notify: Notify }) {
  const client = useQueryClient();
  const router = useRouter();
  const { house } = useCurrentHouse();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!house) return null;
  const current = house;

  async function leave() {
    setBusy(true);
    try {
      await api.post(`/houses/${current.id}/leave`, undefined, { idempotencyKey: newId() });
      forgetHouse();
      await client.invalidateQueries({ queryKey: keys.me });
      router.replace("/");
    } catch (error) {
      notify(errorText(error), "error");
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <section className="kt-field" aria-labelledby="house-heading">
      <h2 id="house-heading" className="kt-field__label">
        {strings.settings.yourHouse}
      </h2>
      <Link href="/house" className="kt-link-row">
        <span className="kt-link-row__text">
          <span className="kt-link-row__title">{strings.settings.houseLink}</span>
          <span className="kt-link-row__hint">{strings.settings.houseLinkHint(current.name)}</span>
        </span>
        <span className="kt-link-row__chevron" aria-hidden="true">
          ›
        </span>
      </Link>
      <PillButton variant="secondary" size={52} fullWidth onClick={() => setConfirming(true)}>
        {strings.settings.leaveHouse(current.name)}
      </PillButton>
      <Dialog
        open={confirming}
        onClose={() => setConfirming(false)}
        title={strings.houseScreen.leaveTitle(current.name)}
        variant="danger"
        confirmLabel={strings.houseScreen.leaveConfirm}
        confirmLoading={busy}
        onConfirm={() => void leave()}
      >
        <p>{strings.houseScreen.leaveBody}</p>
      </Dialog>
    </section>
  );
}

function Appearance() {
  const { preference } = useTheme();
  return (
    <section className="kt-field" aria-labelledby="appearance">
      <h2 id="appearance" className="kt-field__label">
        {strings.settings.appearance}
      </h2>
      <SegmentedControl<ThemePreference>
        label={strings.theme.appearance}
        value={preference}
        onChange={(next) => {
          setPreference(next);
          // Saved to the account too, so other devices follow (PRD §14B ThemeToggle).
          void patchMe({ theme: next }).catch(() => undefined);
        }}
        options={[
          { value: "system", label: strings.theme.system },
          { value: "light", label: strings.theme.light },
          { value: "dark", label: strings.theme.dark },
        ]}
      />
    </section>
  );
}

function DataAndAccount({ notify }: { notify: Notify }) {
  const client = useQueryClient();
  const router = useRouter();
  const typeId = useId();
  const errorId = useId();
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState<"out" | "all" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function leave(path: "/auth/logout" | "/auth/logout-all", kind: "out" | "all") {
    setBusy(kind);
    try {
      await api.post(path, undefined, { retries: 0 });
      forgetHouse();
      client.clear();
      router.replace("/start");
      // Every other tab of this browser too (and, through the stream, every other device).
      announceSignedOut();
    } catch (err) {
      notify(errorText(err), "error");
      setBusy(null);
    }
  }

  async function remove() {
    setBusy("delete");
    setError(null);
    try {
      await api.request("DELETE", "/me", { body: { confirm: typed.trim() }, retries: 0 });
      forgetHouse();
      client.clear();
      router.replace("/start");
      announceSignedOut();
    } catch (err) {
      setError(errorText(err));
      setBusy(null);
    }
  }

  return (
    <section className="kt-field" aria-labelledby="data-heading">
      <h2 id="data-heading" className="kt-field__label">
        {strings.settings.data}
      </h2>
      <a
        className="kt-pill"
        data-variant="secondary"
        data-size="52"
        data-full=""
        href="/api/v1/me/export"
        download
      >
        <span>{strings.settings.export}</span>
      </a>
      <div className="kt-two-up">
        <PillButton
          variant="secondary"
          size={52}
          loading={busy === "out"}
          onClick={() => void leave("/auth/logout", "out")}
        >
          {strings.settings.signOut}
        </PillButton>
        <PillButton
          variant="secondary"
          size={52}
          loading={busy === "all"}
          onClick={() => void leave("/auth/logout-all", "all")}
        >
          {strings.settings.signOutAll}
        </PillButton>
      </div>
      <PillButton variant="danger" size={52} fullWidth onClick={() => setConfirming(true)}>
        {strings.settings.delete}
      </PillButton>
      <Dialog
        open={confirming}
        onClose={() => {
          setConfirming(false);
          setTyped("");
          setError(null);
        }}
        title={strings.settings.deleteTitle}
        variant="danger"
        confirmLabel={strings.settings.delete}
        confirmDisabled={typed.trim() !== strings.settings.deleteWord}
        confirmLoading={busy === "delete"}
        onConfirm={() => void remove()}
      >
        <p>{strings.settings.deleteBody}</p>
        <label htmlFor={typeId} className="kt-field__label">
          {strings.settings.deleteType}
        </label>
        <input
          id={typeId}
          className="kt-input kt-input--big kt-input--mono"
          value={typed}
          autoComplete="off"
          autoCapitalize="characters"
          aria-describedby={error ? errorId : undefined}
          onChange={(e) => setTyped(e.target.value)}
        />
        {error ? (
          <p id={errorId} className="kt-field-error" role="alert">
            {error}
          </p>
        ) : null}
      </Dialog>
    </section>
  );
}
