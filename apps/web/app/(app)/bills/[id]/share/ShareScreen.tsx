"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { MemberAvatar } from "@/components/MemberAvatar";
import { PillButton } from "@/components/PillButton";
import { ShareCard } from "@/components/ShareCard";
import { StampChip } from "@/components/StampChip";
import { ThemeToggle } from "@/components/ThemeToggle";
import { SendIcon } from "@/components/icons";
import { ActionBar } from "@/components/screens/ActionBar";
import { BillGate } from "@/components/screens/BillGate";
import { ScreenToast } from "@/components/screens/ScreenToast";
import { useToast } from "@/components/screens/useToast";
import { useHouse } from "@/lib/api/hooks";
import type { BillView } from "@/lib/api/types";
import { colorIndexOf, shortDate, splitWays } from "@/lib/bills";
import { formatMoney } from "@/lib/format";
import { copyText, displayLink, shareOrCopy } from "@/lib/share";
import { strings } from "@/lib/strings";

export function ShareRoute({ id }: { id: string }) {
  return (
    <BillGate id={id} title={strings.shareScreen.title}>
      {(view) => <ShareScreen view={view} />}
    </BillGate>
  );
}

function ShareScreen({ view }: { view: BillView }) {
  const router = useRouter();
  const billId = view.bill.id;
  const house = useHouse(view.bill.house_id);
  const { toast, show, dismiss } = useToast();
  const [message, setMessage] = useState<string>(strings.shareScreen.defaultMessage);
  const [emailEveryone, setEmailEveryone] = useState(true);
  const [origin, setOrigin] = useState("");
  const currency = view.bill.currency;

  useEffect(() => {
    setOrigin(window.location.origin);
  }, []);
  useEffect(() => {
    if (view.bill.state === "draft") router.replace(`/bills/${billId}/send`);
  }, [view.bill.state, billId, router]);

  const token = view.bill.link_token;
  const url = token && origin ? `${origin}/b/${token}` : "";
  const store = view.bill.store_name ?? strings.bill.untitled;
  const total = formatMoney(view.bill.total_cents, currency);
  const houseName = house.data?.house.name ?? "";
  const meId = view.me.member_id;
  const others = view.participants.filter(
    (p) => p.id !== meId && (view.shares[p.key] ?? 0) !== 0,
  );
  const canEmail = others.some((p) => p.claimed);

  async function share(): Promise<void> {
    if (!url) return;
    const result = await shareOrCopy({ title: `${store} · ${total}`, text: message, url });
    if (result === "copied") show({ message: strings.shareScreen.copied });
  }

  async function copy(): Promise<void> {
    if (url && (await copyText(url))) show({ message: strings.shareScreen.copied });
  }

  async function nudge(amountCents: number): Promise<void> {
    if (!url) return;
    const text = strings.shareScreen.nudgeText(store, shortDate(view.bill.purchase_date), formatMoney(amountCents, currency));
    const result = await shareOrCopy({ title: store, text, url });
    if (result === "copied") show({ message: strings.shareScreen.copied });
  }

  return (
    <div className="kt-screen">
      <div className="kt-share-top">
        <div className="kt-share-top__titles">
          <StampChip rotate={-3}>{strings.shareScreen.sent}</StampChip>
          <h1 className="kt-share-top__title">{strings.shareScreen.title}</h1>
        </div>
        <ThemeToggle />
      </div>
      <main className="kt-screen__main">
        <div className="kt-field">
          <span className="kt-field__label">{strings.share.preview}</span>
          <ShareCard
            kicker={strings.shareScreen.kicker(houseName)}
            title={strings.shareScreen.band(store, total)}
            subline={strings.shareScreen.ways(splitWays(view))}
            message={message}
            onMessageChange={setMessage}
            link={url ? displayLink(url) : "…"}
          />
        </div>
        <div className="kt-two-up">
          <PillButton size={52} onClick={() => void share()} disabled={!url}>
            {strings.shareScreen.share}
          </PillButton>
          <PillButton variant="secondary" size={52} onClick={() => void copy()} disabled={!url}>
            {strings.shareScreen.copyLink}
          </PillButton>
        </div>
        {others.length > 0 ? (
          <div className="kt-field">
            <span className="kt-field__label" id="nudge-heading">
              {strings.shareScreen.nudgeHeading}
            </span>
            <ul className="kt-nudges" aria-labelledby="nudge-heading">
              {others.map((p) => {
                const amount = view.shares[p.key] ?? 0;
                return (
                  <li key={p.key} className="kt-nudge">
                    <MemberAvatar
                      name={p.display_name}
                      initial={p.initial}
                      colorIndex={colorIndexOf(p)}
                      size={32}
                      placeholder={p.kind === "guest"}
                    />
                    <span className="kt-nudge__who">
                      <span className="kt-nudge__name">{p.display_name}</span>
                      <span className="kt-nudge__status">
                        {p.claimed ? strings.shareScreen.signedIn : strings.shareScreen.notJoined}
                      </span>
                    </span>
                    <span className="kt-nudge__amount">{formatMoney(amount, currency)}</span>
                    <button
                      type="button"
                      className="kt-round-button"
                      aria-label={strings.owes.nudge(p.display_name)}
                      onClick={() => void nudge(amount)}
                      disabled={!url}
                    >
                      <SendIcon size={18} />
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}
        <label className="kt-checkbox-row" data-disabled={!canEmail || undefined}>
          <input
            type="checkbox"
            checked={canEmail && emailEveryone}
            disabled={!canEmail}
            aria-describedby={!canEmail ? "email-hint" : undefined}
            onChange={(event) => setEmailEveryone(event.target.checked)}
          />
          <span>
            {strings.shareScreen.email}
            {!canEmail ? (
              <span id="email-hint" className="kt-field__label kt-hint">
                {strings.shareScreen.emailDisabled}
              </span>
            ) : null}
          </span>
        </label>
      </main>
      <ActionBar>
        <PillButton variant="ink" fullWidth href={`/bills/${billId}`}>
          {strings.shareScreen.done}
        </PillButton>
      </ActionBar>
      <ScreenToast toast={toast} onDismiss={dismiss} />
    </div>
  );
}
