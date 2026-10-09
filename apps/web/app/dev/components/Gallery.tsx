"use client";

import Link from "next/link";
import { useCallback, useId, useState, type ReactNode } from "react";
import { ActivityTimeline } from "@/components/ActivityTimeline";
import { AppHeader } from "@/components/AppHeader";
import { BalanceTicker } from "@/components/BalanceTicker";
import { BottomNav } from "@/components/BottomNav";
import { BottomSheet } from "@/components/BottomSheet";
import { Dialog } from "@/components/Dialog";
import { EmptyState } from "@/components/EmptyState";
import { ScanIcon, PlusIcon } from "@/components/icons";
import { ItemRow, type Storage } from "@/components/ItemRow";
import { MemberAvatar, type AvatarSize } from "@/components/MemberAvatar";
import { OTPInput } from "@/components/OTPInput";
import { PersonShareCard } from "@/components/PersonShareCard";
import { PillButton } from "@/components/PillButton";
import { ProgressBar } from "@/components/ProgressBar";
import { ReceiptCard } from "@/components/ReceiptCard";
import { SegmentedControl } from "@/components/SegmentedControl";
import { ShareCard } from "@/components/ShareCard";
import { Skeleton } from "@/components/Skeleton";
import { SplitCheckbox } from "@/components/SplitCheckbox";
import { SplitGrid } from "@/components/SplitGrid";
import { StampChip } from "@/components/StampChip";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Toast } from "@/components/Toast";
import { WhoOwesRow } from "@/components/WhoOwesRow";
import { formatMoney } from "@/lib/format";
import { strings } from "@/lib/strings";
import type { Theme, ThemePreference } from "@/lib/theme";
import {
  ACTIVITY,
  ASSIGNED,
  ASSIGNED_PARTIAL,
  BALANCES,
  HOUSE,
  ITEMS,
  MEMBERS,
  REVIEW_ITEMS,
  SIX_ASSIGNED,
  SIX_MEMBERS,
  SIX_TOTALS,
  TOTALS,
  TOTALS_PARTIAL,
} from "./sample-data";

const SIX_ITEMS = ITEMS.slice(0, 4);
const AVATAR_SIZES: AvatarSize[] = [26, 28, 30, 32, 36];
const EIGHT = ["Pranav", "Arjun", "Meera", "Kabir", "Riya", "Sam", "Tara", "Omar"];

function Demo({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-3 border-t-2 border-dashed border-faint pt-4">
      <h3 className="m-0 font-mono text-[13px] font-bold uppercase">{title}</h3>
      {children}
    </div>
  );
}

function Variant({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <span className="kt-label">{label}</span>
      {children}
    </div>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-3">{children}</div>;
}

function useAssignments(initial: Record<string, string[]>) {
  const [assigned, setAssigned] = useState(initial);
  const [pending, setPending] = useState<Set<string>>(new Set());
  const toggle = useCallback((itemId: string, memberId: string, next: boolean) => {
    // Set-state write with an optimistic tick; the cell stays pending until "the server" acks.
    setAssigned((prev) => {
      const users = prev[itemId] ?? [];
      return {
        ...prev,
        [itemId]: next ? [...users, memberId] : users.filter((id) => id !== memberId),
      };
    });
    const key = `${itemId}:${memberId}`;
    setPending((prev) => new Set(prev).add(key));
    window.setTimeout(() => {
      setPending((prev) => {
        const copy = new Set(prev);
        copy.delete(key);
        return copy;
      });
    }, 600);
  }, []);
  return { assigned, pending, toggle };
}

function Panel({ theme }: { theme: Theme }) {
  const headingId = useId();
  const payer = useAssignments(ASSIGNED);
  const partial = useAssignments(ASSIGNED_PARTIAL);
  const member = useAssignments(ASSIGNED);
  const [single, setSingle] = useState(true);
  const [otp, setOtp] = useState("");
  const [otpPartial, setOtpPartial] = useState("482");
  const [appearance, setAppearance] = useState<ThemePreference>("system");
  const [twoWay, setTwoWay] = useState<"fridge" | "pantry">("fridge");
  const [storage, setStorage] = useState<Record<string, Storage>>(
    Object.fromEntries(REVIEW_ITEMS.map((item) => [item.id, item.storage])),
  );
  const [editing, setEditing] = useState<string | null>("towels");
  const [message, setMessage] = useState("Groceries are in. Tick anything I got wrong.");
  const [sheetOpen, setSheetOpen] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dangerOpen, setDangerOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const partialLeft = ITEMS.filter((item) => (partial.assigned[item.id] ?? []).length === 0).length;

  return (
    <section
      data-theme={theme}
      aria-labelledby={headingId}
      className="flex min-w-0 flex-col gap-6 rounded-card border-2 border-ink px-4 pt-4 pb-8 sm:px-5"
    >
      <h2 id={headingId} className="m-0 font-mono text-xl font-bold">
        {theme === "light" ? strings.dev.lightPanel : strings.dev.darkPanel}
      </h2>

      <Demo title="AppHeader">
        <Variant label="With step label and back">
          <div className="-mx-4 border-y-2 border-dashed border-faint sm:-mx-5">
            <AppHeader
              headingLevel={3}
              previewTheme={theme}
              back={{ href: "/dev/components", label: "Back to receipt" }}
              stepLabel="STEP 2/3 · SHOPRITE · 10/02"
              title={strings.split.title}
            >
              <ProgressBar value={9} max={12} />
            </AppHeader>
          </div>
        </Variant>
        <Variant label="With subtitle and more menu">
          <div className="-mx-4 border-b-2 border-dashed border-faint sm:-mx-5">
            <AppHeader
              headingLevel={3}
              previewTheme={theme}
              back={{ href: "/dev/components", label: "Back to tabs" }}
              title="ShopRite"
              subtitle={`10/02 · YOU PAID · ${formatMoney(9518)}`}
              onMore={() => setToast("More actions")}
            />
          </div>
        </Variant>
        <Variant label="Large, with scan action">
          <div className="-mx-4 border-b-2 border-dashed border-faint sm:-mx-5">
            <AppHeader
              headingLevel={3}
              large
              previewTheme={theme}
              stepLabel={strings.app.brandLine(HOUSE)}
              title={strings.kitchen.title}
              actions={
                <Link
                  href="/dev/components"
                  aria-label={strings.header.snapReceipt}
                  className="kt-icon-button"
                  data-variant="accent"
                >
                  <ScanIcon size={20} />
                </Link>
              }
            />
          </div>
        </Variant>
      </Demo>

      <Demo title="ThemeToggle">
        <Row>
          <ThemeToggle previewTheme={theme} />
          <ThemeToggle previewTheme={theme} withShadow />
        </Row>
      </Demo>

      <Demo title="PillButton">
        <div className="flex flex-col gap-4">
          <PillButton variant="primary" fullWidth>
            Stamp it settled
          </PillButton>
          <PillButton variant="primary" pressed fullWidth>
            Pressed
          </PillButton>
          <PillButton variant="ink" size={52} fullWidth>
            Done → see the tab
          </PillButton>
          <Row>
            <PillButton variant="secondary" size={52}>
              Edit splits
            </PillButton>
            <PillButton variant="danger" size={52}>
              Void this tab
            </PillButton>
          </Row>
          <PillButton variant="dashed" size={44} fullWidth icon={<PlusIcon size={16} />}>
            Split the rest with everyone
          </PillButton>
          <PillButton variant="primary" disabled disabledReason="Give every item a home first." fullWidth>
            All sorted → review
          </PillButton>
          <PillButton variant="primary" loading fullWidth>
            Sending
          </PillButton>
        </div>
      </Demo>

      <Demo title="ReceiptCard">
        <ReceiptCard heading="Plain" shadow={false}>
          <p className="m-0 text-[13px]">SHOPRITE · 10/02/2026 · 12 ITEMS</p>
        </ReceiptCard>
        <ReceiptCard heading="Rotated hero" rotated>
          <div className="flex items-baseline justify-between">
            <span className="text-lg font-bold">SHOPRITE</span>
            <span className="kt-money text-lg font-bold">{formatMoney(9518)}</span>
          </div>
        </ReceiptCard>
        <ReceiptCard
          heading={strings.owes.heading}
          stamp={<StampChip rotate={3}>{strings.owes.stillOpen(2)}</StampChip>}
        >
          <p className="m-0 text-[13px] text-muted">The stamp slot pins a chip to the corner.</p>
        </ReceiptCard>
        <ReceiptCard
          heading="Settled"
          overlay={
            <StampChip large rotate={-4} animate>
              {strings.stamp.settled("10/05")}
            </StampChip>
          }
        >
          <p className="m-0 py-6 text-[13px]">Everyone&apos;s square.</p>
        </ReceiptCard>
        <ReceiptCard
          heading="Voided"
          muted
          overlay={
            <StampChip large tone="muted" rotate={3}>
              {strings.stamp.voided}
            </StampChip>
          }
        >
          <p className="m-0 py-6 text-[13px] line-through">{formatMoney(6240)}</p>
        </ReceiptCard>
      </Demo>

      <Demo title="StampChip">
        <Row>
          <StampChip rotate={-2}>1/3 PAID</StampChip>
          <StampChip tone="warn" rotate={1.5}>
            YOU OWE {formatMoney(1410)}
          </StampChip>
          <StampChip tone="muted" rotate={-4}>
            SETTLED
          </StampChip>
          <StampChip rotate={4}>CHECK?</StampChip>
        </Row>
        <Row>
          <StampChip large rotate={-3}>
            {strings.stamp.settled("10/05")}
          </StampChip>
          <StampChip large tone="muted" rotate={2}>
            {strings.stamp.voided}
          </StampChip>
        </Row>
      </Demo>

      <Demo title="MemberAvatar">
        <Variant label="Sizes 26 · 28 · 30 · 32 · 36">
          <Row>
            {AVATAR_SIZES.map((size, i) => (
              <MemberAvatar key={size} name={MEMBERS[i % 4]!.name} colorIndex={i % 4} size={size} decorative={false} />
            ))}
          </Row>
        </Variant>
        <Variant label="All eight member colors, then a placeholder">
          <Row>
            {EIGHT.map((name, i) => (
              <MemberAvatar key={name} name={name} colorIndex={i} size={32} decorative={false} />
            ))}
            <MemberAvatar name="Dev" colorIndex={0} size={32} placeholder decorative={false} />
          </Row>
        </Variant>
      </Demo>

      <Demo title="SplitCheckbox">
        <Row>
          <SplitCheckbox
            memberName="Pranav"
            itemName="Whole milk"
            initial="P"
            colorIndex={0}
            checked={single}
            onToggle={() => setSingle((v) => !v)}
            row={0}
            col={0}
          />
          <SplitCheckbox memberName="Arjun" itemName="Whole milk" initial="A" colorIndex={1} checked row={0} col={1} />
          <SplitCheckbox memberName="Meera" itemName="Whole milk" initial="M" colorIndex={2} checked={false} row={0} col={2} />
          <SplitCheckbox memberName="Kabir" itemName="Whole milk" initial="K" colorIndex={3} checked disabled row={0} col={3} />
          <SplitCheckbox memberName="Riya" itemName="Whole milk" initial="R" colorIndex={4} checked pending row={1} col={3} />
          <SplitCheckbox memberName="Sam" itemName="Whole milk" initial="S" colorIndex={5} checked={false} disabled row={1} col={4} />
        </Row>
        <p className="m-0 text-[13px] text-muted">On · on · off · disabled · pending · disabled off</p>
      </Demo>

      <Demo title="ProgressBar">
        <ProgressBar value={9} max={12} />
        <ProgressBar value={0} max={12} />
        <ProgressBar value={12} max={12} />
      </Demo>

      <Demo title="SplitGrid">
        <Variant label="Payer mode, all assigned">
          <div className="-mx-4 sm:-mx-5">
            <SplitGrid
              members={MEMBERS}
              items={ITEMS}
              assigned={payer.assigned}
              pending={payer.pending}
              totals={TOTALS}
              onToggle={payer.toggle}
              note="TAX $0.89 · SPLIT BY EACH PERSON'S SHARE"
              footer={
                <Link href="/dev/components" className="kt-split__done">
                  {strings.split.allSorted}
                </Link>
              }
            />
          </div>
        </Variant>
        <Variant label="Payer mode, items left">
          <div className="-mx-4 sm:-mx-5">
            <SplitGrid
              members={MEMBERS}
              items={ITEMS.slice(8)}
              assigned={partial.assigned}
              pending={partial.pending}
              totals={TOTALS_PARTIAL}
              onToggle={partial.toggle}
              footer={<div className="kt-split__left">{strings.split.itemsLeft(partialLeft)}</div>}
            />
          </div>
        </Variant>
        <Variant label="Member mode (Arjun's column only)">
          <div className="-mx-4 sm:-mx-5">
            <SplitGrid
              members={MEMBERS}
              items={ITEMS.slice(0, 4)}
              assigned={member.assigned}
              pending={member.pending}
              totals={TOTALS}
              mode="member"
              currentMemberId="arjun"
              onToggle={member.toggle}
              conflict={
                <span>
                  {strings.split.conflict}
                </span>
              }
            />
          </div>
        </Variant>
        <Variant label="Read-only">
          <div className="-mx-4 sm:-mx-5">
            <SplitGrid members={MEMBERS} items={ITEMS.slice(0, 3)} assigned={ASSIGNED} totals={TOTALS} mode="readonly" />
          </div>
        </Variant>
        <Variant label="Six members: columns scroll, item column sticks">
          <div className="-mx-4 sm:-mx-5">
            <SplitGrid members={SIX_MEMBERS} items={SIX_ITEMS} assigned={SIX_ASSIGNED} totals={SIX_TOTALS} />
          </div>
        </Variant>
        <Variant label="Loading">
          <div className="-mx-4 sm:-mx-5">
            <SplitGrid members={MEMBERS} items={[]} assigned={{}} loading />
          </div>
        </Variant>
      </Demo>

      <Demo title="OTPInput">
        <Variant label="Empty (focus to see the active box)">
          <OTPInput value={otp} onChange={setOtp} label="Code, empty example" />
        </Variant>
        <Variant label="Part filled">
          <OTPInput value={otpPartial} onChange={setOtpPartial} label="Code, part filled example" />
        </Variant>
        <Variant label="Filled">
          <OTPInput value="482913" onChange={() => undefined} label="Code, filled example" />
        </Variant>
        <Variant label="Error">
          <OTPInput value="482910" onChange={() => undefined} error={strings.otp.error} label="Code, error example" />
        </Variant>
      </Demo>

      <Demo title="SegmentedControl">
        <SegmentedControl
          label={strings.theme.appearance}
          value={appearance}
          onChange={setAppearance}
          options={[
            { value: "system", label: strings.theme.system },
            { value: "light", label: strings.theme.light },
            { value: "dark", label: strings.theme.dark },
          ]}
        />
        <SegmentedControl
          label="Where it goes"
          value={twoWay}
          onChange={setTwoWay}
          options={[
            { value: "fridge", label: "FRIDGE" },
            { value: "pantry", label: "PANTRY" },
          ]}
        />
      </Demo>

      <Demo title="ItemRow">
        <p className="m-0 text-center font-mono text-[10px] text-muted">{strings.review.tagsHelp}</p>
        <div className="flex flex-col">
          {REVIEW_ITEMS.map((item) => (
            <ItemRow
              key={item.id}
              name={item.name}
              priceCents={item.priceCents}
              flagged={item.flagged}
              storage={storage[item.id] ?? item.storage}
              onStorageChange={(next) => setStorage((prev) => ({ ...prev, [item.id]: next }))}
              editing={editing === item.id}
              onEdit={() => setEditing(item.id)}
              onSave={() => setEditing(null)}
              onCancel={() => setEditing(null)}
            />
          ))}
        </div>
      </Demo>

      <Demo title="PersonShareCard">
        <PersonShareCard
          name="Pranav"
          colorIndex={0}
          itemCount={9}
          amountCents={2351}
          variant="payer"
          items="Whole milk, Eggs, Basmati rice, Bananas, Paper towels, Dish soap, Chicken thighs, Baby spinach, Coffee beans"
        />
        <PersonShareCard
          name="Arjun"
          colorIndex={1}
          itemCount={7}
          amountCents={3391}
          items="Whole milk, Eggs, Basmati rice, Paper towels, Dish soap, Chicken thighs, Protein bars"
        />
        <PersonShareCard name="Dev (guest)" colorIndex={4} itemCount={1} amountCents={1022} variant="guest" items="Coffee beans" />
      </Demo>

      <Demo title="WhoOwesRow">
        <ul className="kt-owes-list">
          <WhoOwesRow name="You" colorIndex={0} amountCents={2351} status="payer" />
          <WhoOwesRow name="Arjun" colorIndex={1} amountCents={3391} status="unpaid" onNudge={() => setToast("Nudged Arjun.")} />
          <WhoOwesRow
            name="Meera"
            colorIndex={2}
            amountCents={1887}
            status="pending"
            action={
              <>
                <PillButton variant="primary" size={44} className="kt-pill--compact">
                  {strings.owes.confirm}
                </PillButton>
                <PillButton variant="secondary" size={44} className="kt-pill--compact">
                  {strings.owes.reject}
                </PillButton>
              </>
            }
          />
          <WhoOwesRow name="Kabir" colorIndex={3} amountCents={1889} status="paid" />
          <WhoOwesRow name="Riya" colorIndex={4} amountCents={1721} status="credit" statusAmountCents={168} />
        </ul>
      </Demo>

      <Demo title="ActivityTimeline">
        <ActivityTimeline events={ACTIVITY} limit={3} />
      </Demo>

      <Demo title="BalanceTicker">
        <BalanceTicker lines={BALANCES} footnote={strings.balance.netted} />
      </Demo>

      <Demo title="ShareCard">
        <ShareCard
          kicker={`KITCHENTAB · ${HOUSE.toUpperCase()}`}
          title={`SHOPRITE · ${formatMoney(9518)}`}
          subline="SPLIT 4 WAYS · TAP FOR YOUR SHARE"
          message={message}
          onMessageChange={setMessage}
          link="kitchentab.app/b/Zp4mQ81c"
        />
        <ShareCard
          variant="invite"
          kicker={`KITCHENTAB · ${HOUSE.toUpperCase()}`}
          title="JOIN APT 4B"
          subline="TAP TO CLAIM YOUR SPOT"
          message="Join our house on KitchenTab so we can split groceries."
          link="kitchentab.app/h/Q7wLm2xa"
        />
        <ShareCard
          variant="nudge"
          kicker="KITCHENTAB · FOR ARJUN"
          title={`YOUR SHARE · ${formatMoney(3391)}`}
          message="Your ShopRite share is ready."
          link="kitchentab.app/b/Zp4mQ81c?m=arjun"
        />
      </Demo>

      <Demo title="BottomSheet · Dialog · Toast">
        <Row>
          <PillButton variant="secondary" size={44} onClick={() => setSheetOpen(true)}>
            {strings.dev.openSheet}
          </PillButton>
          <PillButton variant="secondary" size={44} onClick={() => setDialogOpen(true)}>
            {strings.dev.openDialog}
          </PillButton>
          <PillButton variant="secondary" size={44} onClick={() => setDangerOpen(true)}>
            {strings.dev.openDangerDialog}
          </PillButton>
          <PillButton
            variant="secondary"
            size={44}
            onClick={() => setToast("Arjun removed from Coffee beans.")}
          >
            {strings.dev.showToast}
          </PillButton>
        </Row>
        <BottomSheet
          open={sheetOpen}
          onClose={() => setSheetOpen(false)}
          title={`Pay Pranav ${formatMoney(3391)}`}
          footer={
            <PillButton variant="primary" fullWidth onClick={() => setSheetOpen(false)}>
              I&apos;ve paid — mark it
            </PillButton>
          }
        >
          <p className="m-0 text-[15px] leading-normal">
            Pranav hasn&apos;t added payment info. Pay however you usually do, then mark it.
          </p>
          <div className="mt-4">
            <PillButton variant="secondary" size={52} fullWidth onClick={() => undefined}>
              Copy amount
            </PillButton>
          </div>
        </BottomSheet>
        <Dialog
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          title="Stamp it settled?"
          confirmLabel="Stamp it"
          onConfirm={() => setDialogOpen(false)}
        >
          Everyone&apos;s square.
        </Dialog>
        <Dialog
          open={dangerOpen}
          onClose={() => setDangerOpen(false)}
          title="Void this tab?"
          variant="danger"
          confirmLabel="Void this tab"
          onConfirm={() => setDangerOpen(false)}
        >
          Nobody will owe anything on it. The receipt stays in the history.
        </Dialog>
        <Variant label="Toast: info, with Undo, error">
          <Toast placement="inline" message="Marked. Pranav will confirm." duration={Infinity} />
          <Toast placement="inline" message="Arjun removed from Coffee beans." onUndo={() => undefined} duration={Infinity} />
          <Toast placement="inline" variant="error" message="Something jammed. Try again." duration={Infinity} />
        </Variant>
        <Toast message={toast} onUndo={() => undefined} onDismiss={() => setToast(null)} />
      </Demo>

      <Demo title="BottomNav">
        <div className="-mx-4 flex flex-col gap-3 sm:-mx-5">
          <BottomNav current="/" sticky={false} />
          <BottomNav current="/tabs" sticky={false} />
        </div>
      </Demo>

      <Demo title="Skeleton">
        <Skeleton lines={3} />
        <Skeleton variant="card" lines={4} />
      </Demo>

      <Demo title="EmptyState">
        <EmptyState
          headingLevel={4}
          title={strings.kitchen.emptyTitle}
          body={strings.kitchen.comingSoon}
          action={
            <PillButton variant="dashed" size={44} href="/dev/components" icon={<ScanIcon size={16} />}>
              {strings.header.snapReceipt}
            </PillButton>
          }
        />
      </Demo>
    </section>
  );
}

export function Gallery() {
  return (
    <div className="mx-auto flex min-h-dvh max-w-[1400px] flex-col">
      <AppHeader title={strings.dev.title} stepLabel={strings.dev.kicker} />
      <main className="grid grid-cols-1 gap-6 px-4 pb-16 min-[900px]:grid-cols-2">
        <Panel theme="light" />
        <Panel theme="dark" />
      </main>
    </div>
  );
}
