"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { PillButton } from "@/components/PillButton";
import { Skeleton } from "@/components/Skeleton";
import { Switch } from "@/components/Switch";
import { Toast } from "@/components/Toast";
import { ErrorCard } from "@/components/screens/ErrorCard";
import { api, isApiError } from "@/lib/api/client";
import { useMe } from "@/lib/api/hooks";
import {
  deviceName,
  isDesktopSafari,
  isIos,
  isStandalone,
  notificationPermission,
  pushSupported,
} from "@/lib/pwa/device";
import { openN1, promptInstall, usePrompts } from "@/lib/pwa/prompts";
import { currentDeviceHash, type Device } from "@/lib/pwa/push";
import { strings } from "@/lib/strings";
import { useOnline } from "@/lib/sync/online";

const S = strings.notify;

export type PrefType = keyof typeof S.types;
const TYPES: PrefType[] = ["asks", "answers", "share", "tab", "pay", "remind", "soon", "settled"];

export type Prefs = {
  push: Record<string, boolean>;
  email: Record<string, boolean>;
  quiet_hours: boolean;
  quiet_start: string;
  quiet_end: string;
  private_lock_screen: boolean;
  timezone: string | null;
};

export const prefsKey = ["me", "notification-prefs"] as const;
export const devicesKey = ["push", "devices"] as const;

/** "22:00" → "10 PM", "08:30" → "8:30 AM" (N2: "Quiet hours · 10 PM – 8 AM"). */
export function clockLabel(hhmm: string): string {
  const [h = "0", m = "00"] = hhmm.split(":");
  const hour = Number(h);
  const suffix = hour >= 12 ? "PM" : "AM";
  const twelve = hour % 12 === 0 ? 12 : hour % 12;
  return m === "00" ? `${twelve} ${suffix}` : `${twelve}:${m} ${suffix}`;
}

function sinceLabel(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" }).toUpperCase();
}

type Notify = (message: string, retry?: () => void) => void;

/** N2 · Settings › Notifications (NotifySettings.dc.html, PRD §20.2). */
export function NotifySettingsScreen() {
  const me = useMe();
  const prefs = useQuery({ queryKey: prefsKey, queryFn: () => api.get<Prefs>("/me/notification-prefs") });
  const [toast, setToast] = useState<{ message: string; retry?: () => void } | null>(null);
  const notify: Notify = (message, retry) => setToast({ message, retry });
  const admin = me.data?.houses.some((h) => h.role === "admin") ?? false;
  return (
    <div className="kt-screen kt-n2">
      <AppHeader
        stepLabel={S.step}
        title={S.screenTitle}
        back={{ href: "/settings", label: S.backToSettings }}
      />
      <main className="kt-screen__main">
        <PushStatus notify={notify} />
        {prefs.isError ? <ErrorCard error={prefs.error} onRetry={() => void prefs.refetch()} /> : null}
        {prefs.isPending ? <Skeleton variant="card" lines={8} /> : null}
        {prefs.data ? (
          <>
            <Matrix prefs={prefs.data} types={admin ? [...TYPES, "joined"] : TYPES} notify={notify} />
            <QuietAndPrivate prefs={prefs.data} notify={notify} />
          </>
        ) : null}
        <Devices notify={notify} />
        <Install />
        <p className="kt-footnote kt-n2__footer">
          {S.footer[0]}
          <br />
          {S.footer[1]}
        </p>
      </main>
      <Toast
        message={toast?.message ?? null}
        variant={toast?.retry ? "error" : "info"}
        actionLabel={toast?.retry ? strings.error.retry : undefined}
        onUndo={toast?.retry}
        onDismiss={() => setToast(null)}
      />
    </div>
  );
}

function PushStatus({ notify }: { notify: Notify }) {
  const online = useOnline();
  const devices = useQuery({ queryKey: devicesKey, queryFn: () => api.get<Device[]>("/push/subscriptions") });
  const [hash, setHash] = useState<string | null | undefined>(undefined);
  const [env, setEnv] = useState<{ permission: string; supported: boolean; ios: boolean; standalone: boolean; name: string } | null>(null);
  const [sending, setSending] = useState(false);
  const { n1 } = usePrompts();

  useEffect(() => {
    setEnv({
      permission: notificationPermission(),
      supported: pushSupported(),
      ios: isIos(),
      standalone: isStandalone(),
      name: deviceName(),
    });
    void currentDeviceHash().then(setHash);
  }, [n1]);

  if (!env) return <Skeleton variant="card" lines={2} />;
  const here = Boolean(hash && devices.data?.some((d) => d.endpoint_hash === hash));
  const on = env.permission === "granted" && here;
  const blocked = env.permission === "denied";
  const iosTab = env.ios && !env.standalone;
  const stamp = on ? S.pushOn : blocked ? S.pushBlocked : S.pushOff;
  const line = on
    ? env.standalone
      ? S.hereStandalone(env.name)
      : S.hereBrowser(env.name)
    : blocked
      ? S.blockedHelp
      : iosTab || !env.supported
        ? S.unsupported
        : S.offHere;

  async function sendTest(): Promise<void> {
    setSending(true);
    try {
      await api.post("/push/test");
      notify(S.testSent);
    } catch (error) {
      notify(isApiError(error) ? error.title : strings.error.couldntSave, () => void sendTest());
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="kt-n2__status" aria-label={stamp}>
      <div className="kt-n2__status-text">
        <span className="kt-n2__stamp" data-on={on || undefined}>
          {stamp}
        </span>
        <span className="kt-n2__status-line">{line}</span>
      </div>
      {on ? (
        <PillButton
          variant="secondary"
          size={44}
          loading={sending}
          disabled={!online}
          disabledReason={online ? undefined : strings.button.needsConnection}
          onClick={() => void sendTest()}
        >
          {S.sendTest}
        </PillButton>
      ) : blocked ? null : (
        <PillButton
          variant="secondary"
          size={44}
          disabled={!online}
          onClick={() =>
            openN1({ platform: env.ios ? "ios" : "android", countsAsInstall: false, fromSettings: true })
          }
        >
          {iosTab || !env.supported ? S.showMe : S.turnOnShort}
        </PillButton>
      )}
    </section>
  );
}

function usePrefWrite(notify: Notify) {
  const client = useQueryClient();
  return async function write(change: Partial<Prefs> & { push?: Record<string, boolean>; email?: Record<string, boolean> }) {
    const before = client.getQueryData<Prefs>(prefsKey);
    // Set-state write (PRD §21.3): the desired value, optimistic, rolled back on failure.
    client.setQueryData<Prefs>(prefsKey, (old) =>
      old
        ? {
            ...old,
            ...change,
            push: { ...old.push, ...change.push },
            email: { ...old.email, ...change.email },
          }
        : old,
    );
    try {
      const saved = await api.put<Prefs>("/me/notification-prefs", change);
      client.setQueryData(prefsKey, saved);
    } catch (error) {
      client.setQueryData(prefsKey, before);
      notify(isApiError(error) && error.status !== 0 ? error.title : strings.sync.couldntSaveRetry, () => void write(change));
    }
  };
}

function Toggle({ on, label, disabled, onChange }: { on: boolean; label: string; disabled: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      className="kt-n2__toggle"
      aria-pressed={on}
      aria-label={label}
      disabled={disabled}
      title={disabled ? strings.button.needsConnection : undefined}
      onClick={() => onChange(!on)}
    >
      <span className="kt-n2__track" aria-hidden="true">
        <span className="kt-n2__knob" />
      </span>
    </button>
  );
}

function Matrix({ prefs, types, notify }: { prefs: Prefs; types: PrefType[]; notify: Notify }) {
  const online = useOnline();
  const write = usePrefWrite(notify);
  const headingId = useId();
  return (
    <section className="kt-n2__matrix" aria-labelledby={headingId}>
      <div className="kt-n2__matrix-head">
        <h2 id={headingId} className="kt-n2__label">
          {S.pingMeAbout}
        </h2>
        <span className="kt-n2__col" aria-hidden="true">
          {S.push}
        </span>
        <span className="kt-n2__col" aria-hidden="true">
          {S.email}
        </span>
      </div>
      <ul className="kt-n2__rows">
        {types.map((type) => {
          const { label, hint } = S.types[type];
          return (
            <li key={type} className="kt-n2__row">
              <span className="kt-n2__row-text">
                <span className="kt-n2__row-label">{label}</span>
                {hint ? <span className="kt-n2__row-hint">{hint}</span> : null}
              </span>
              <Toggle
                on={Boolean(prefs.push[type])}
                label={S.pushFor(label)}
                disabled={!online}
                onChange={(v) => void write({ push: { [type]: v } })}
              />
              <Toggle
                on={Boolean(prefs.email[type])}
                label={S.emailFor(label)}
                disabled={!online}
                onChange={(v) => void write({ email: { [type]: v } })}
              />
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function QuietAndPrivate({ prefs, notify }: { prefs: Prefs; notify: Notify }) {
  const online = useOnline();
  const write = usePrefWrite(notify);
  const quietHint = useId();
  const privHint = useId();
  return (
    <section className="kt-n2__card" aria-label={S.quietLabel}>
      <div className="kt-share-row kt-n2__switch-row">
        <div className="kt-share-row__text">
          <span className="kt-share-row__title">
            {S.quiet(clockLabel(prefs.quiet_start), clockLabel(prefs.quiet_end))}
          </span>
          <span id={quietHint} className="kt-share-row__note">
            {S.quietHint}
          </span>
        </div>
        <Switch
          checked={prefs.quiet_hours}
          label={S.quietLabel}
          describedBy={quietHint}
          disabled={!online}
          onChange={(v) => void write({ quiet_hours: v })}
        />
      </div>
      <div className="kt-share-row kt-n2__switch-row">
        <div className="kt-share-row__text">
          <span className="kt-share-row__title">{S.private}</span>
          <span id={privHint} className="kt-share-row__note">
            {S.privateHint}
          </span>
        </div>
        <Switch
          checked={prefs.private_lock_screen}
          label={S.private}
          describedBy={privHint}
          disabled={!online}
          onChange={(v) => void write({ private_lock_screen: v })}
        />
      </div>
    </section>
  );
}

function Devices({ notify }: { notify: Notify }) {
  const client = useQueryClient();
  const online = useOnline();
  const devices = useQuery({ queryKey: devicesKey, queryFn: () => api.get<Device[]>("/push/subscriptions") });
  const [hash, setHash] = useState<string | null>(null);
  const [standalone, setStandalone] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const headingId = useId();
  useEffect(() => {
    setStandalone(isStandalone());
    void currentDeviceHash().then(setHash);
  }, [devices.data]);

  async function remove(device: Device): Promise<void> {
    setRemoving(device.id);
    try {
      await api.del(`/push/subscriptions/${device.id}`);
    } catch (error) {
      notify(isApiError(error) ? error.title : strings.error.couldntSave, () => void remove(device));
    } finally {
      setRemoving(null);
      await client.invalidateQueries({ queryKey: devicesKey });
    }
  }

  return (
    <section className="kt-field" aria-labelledby={headingId}>
      <h2 id={headingId} className="kt-n2__label">
        {S.devices}
      </h2>
      {devices.isPending ? <Skeleton lines={2} /> : null}
      {devices.data && devices.data.length === 0 ? <p className="kt-n2__empty">{S.noDevices}</p> : null}
      {devices.data && devices.data.length > 0 ? (
        <ul className="kt-n2__devices">
          {devices.data.map((d) => {
            const mine = d.endpoint_hash === hash;
            const kind = mine ? (standalone ? S.homeScreen : S.browser) : null;
            const date = sinceLabel(d.created_at);
            return (
              <li key={d.id} className="kt-n2__device">
                <span className="kt-n2__device-text">
                  <span className="kt-n2__device-name">{d.device_label}</span>
                  <span className="kt-n2__device-meta">
                    {kind ? S.since(kind, date) : `SINCE ${date}`}
                    {d.stale ? ` · ${S.stale}` : ""}
                  </span>
                </span>
                {mine ? (
                  <span className="kt-n2__here">{S.here}</span>
                ) : (
                  <PillButton
                    variant="secondary"
                    size={44}
                    aria-label={S.removeLabel(d.device_label)}
                    loading={removing === d.id}
                    disabled={!online}
                    onClick={() => void remove(d)}
                  >
                    {S.remove}
                  </PillButton>
                )}
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}

/** Settings › Install app: where the install card goes after it was shown twice (PRD §20.1). */
function Install() {
  const { canInstall } = usePrompts();
  const [env, setEnv] = useState<{ standalone: boolean; ios: boolean; safari: boolean } | null>(null);
  const headingId = useId();
  useEffect(() => setEnv({ standalone: isStandalone(), ios: isIos(), safari: isDesktopSafari() }), [canInstall]);
  if (!env) return null;
  return (
    <section className="kt-field" aria-labelledby={headingId}>
      <h2 id={headingId} className="kt-n2__label">
        {S.install}
      </h2>
      {env.standalone ? (
        <p className="kt-n2__hint">{S.installed}</p>
      ) : canInstall ? (
        <PillButton variant="secondary" size={52} fullWidth onClick={() => void promptInstall()}>
          {S.installButton}
        </PillButton>
      ) : (
        <p className="kt-n2__hint">{env.ios ? S.iosHint : env.safari ? S.safariHint : S.otherHint}</p>
      )}
    </section>
  );
}
