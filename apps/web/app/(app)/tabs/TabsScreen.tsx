"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { BalanceTicker } from "@/components/BalanceTicker";
import { BottomNav } from "@/components/BottomNav";
import { EmptyState } from "@/components/EmptyState";
import { MemberAvatar } from "@/components/MemberAvatar";
import { PillButton } from "@/components/PillButton";
import { Skeleton } from "@/components/Skeleton";
import { StampChip } from "@/components/StampChip";
import { ScanIcon } from "@/components/icons";
import { ErrorCard } from "@/components/screens/ErrorCard";
import { HouseGate, type HouseContext } from "@/components/screens/HouseGate";
import { HouseHeader } from "@/components/screens/HouseHeader";
import { useBalances, useSearch, useTabs } from "@/lib/api/tabs-hooks";
import type { TabCard as Card } from "@/lib/api/tabs-types";
import { formatMoney } from "@/lib/format";
import { strings } from "@/lib/strings";
import { useServerNow } from "@/lib/use-server-time";
import {
  balanceHeadline,
  cardMeta,
  changeNote,
  firstName,
  monDay,
  pairLines,
  stampDate,
  tabStamp,
} from "@/lib/tabs";

const PAIR_ROWS = 4;
const SETTLED_PREVIEW = 3;

/** 1 · Tabs (Main.dc.html, PRD §14A 1, FR-50..FR-53). */
export function TabsScreen() {
  return <HouseGate title={strings.nav.tabs}>{(ctx) => <Tabs {...ctx} />}</HouseGate>;
}

function Tabs({ house, houses, me, select }: HouseContext) {
  const tabs = useTabs(house.id);
  const balances = useBalances(house.id);
  const [allHistory, setAllHistory] = useState(false);
  // Pages of history asked for. A live update's refetch cancels an in-flight "Show 20 more" and
  // reloads only the pages already there, so keep fetching until the asked-for pages are in (VIS-03).
  const [wantPages, setWantPages] = useState(1);
  const pagesLoaded = tabs.data?.pages.length ?? 0;
  const { hasNextPage, isFetching, fetchNextPage } = tabs;
  useEffect(() => {
    if (pagesLoaded > 0 && pagesLoaded < wantPages && hasNextPage && !isFetching)
      void fetchNextPage();
  }, [pagesLoaded, wantPages, hasNextPage, isFetching, fetchNextPage]);
  const [query, setQuery] = useState("");
  const serverNow = useServerNow(60_000);
  const now = new Date(serverNow);
  const first = tabs.data?.pages[0];
  const history = tabs.data?.pages.flatMap((p) => p.history) ?? [];
  const shownHistory = allHistory ? history : history.slice(0, SETTLED_PREVIEW);
  const empty =
    first && first.drafts.length === 0 && first.open.length === 0 && history.length === 0;
  const meId = house.member_id;

  return (
    <div className="kt-screen kt-tabs" data-wide="">
      <HouseHeader
        house={house}
        houses={houses}
        onSelect={select}
        title={strings.tabs.hey(firstName(me.user.display_name))}
        actions={
          <Link href="/settings" className="kt-avatar-link" aria-label={strings.tabs.settingsLabel}>
            <MemberAvatar name={me.user.display_name} colorIndex={0} size={36} />
          </Link>
        }
      />
      <main className="kt-screen__main kt-tabs__main">
        <div className="kt-tabs__side">
          <BalanceCard balances={balances} now={now} />
          <PillButton
            href={`/bills/new?house=${house.id}`}
            fullWidth
            icon={<ScanIcon size={22} />}
            className="kt-snap"
          >
            {strings.landing.snap}
          </PillButton>
          <Search houseId={house.id} query={query} onQuery={setQuery} />
        </div>
        <div className="kt-tabs__lists">
          {tabs.isPending ? <Skeleton variant="card" lines={4} /> : null}
          {tabs.isError ? (
            <ErrorCard error={tabs.error} onRetry={() => void tabs.refetch()} />
          ) : null}
          {empty ? (
            <EmptyState
              title={strings.tabs.empty}
              action={
                <PillButton href={`/bills/new?house=${house.id}`}>
                  {strings.tabs.snapFirst}
                </PillButton>
              }
            />
          ) : null}
          {first && first.drafts.length > 0 ? (
            <ul className="kt-list">
              {first.drafts.map((b) => (
                <li key={b.id}>
                  <Link href={`/bills/${b.id}/review`} className="kt-tab-card" data-draft="">
                    <span className="kt-tab-card__store">
                      {strings.bill.draft(b.store_name ?? strings.bill.untitled)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : null}
          {first && first.open.length > 0 ? (
            <section className="kt-tabs__section" aria-labelledby="open-tabs">
              <h2 id="open-tabs" className="kt-list-heading">
                <span>{strings.tabs.openTabs}</span>
                <span className="kt-list-heading__count">{first.open.length}</span>
              </h2>
              <ul className="kt-list">
                {first.open.map((card) => (
                  <li key={card.id}>
                    <OpenCard card={card} meId={meId} now={now} />
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          {history.length > 0 ? (
            <section className="kt-tabs__section" aria-labelledby="settled-tabs">
              <h2 id="settled-tabs" className="sr-only">
                {strings.tabs.settledHeading}
              </h2>
              <ul className="kt-list" data-testid="settled-list">
                {shownHistory.map((card, i) => (
                  <li key={card.id}>
                    <SettledCard card={card} tilt={i} />
                  </li>
                ))}
              </ul>
              {!allHistory && (history.length > SETTLED_PREVIEW || tabs.hasNextPage) ? (
                <PillButton variant="dashed" fullWidth onClick={() => setAllHistory(true)}>
                  {strings.tabs.showAll}
                </PillButton>
              ) : null}
              {allHistory && tabs.hasNextPage ? (
                <PillButton
                  variant="dashed"
                  fullWidth
                  loading={tabs.isFetchingNextPage || pagesLoaded < wantPages}
                  onClick={() => setWantPages(pagesLoaded + 1)}
                >
                  {strings.tabs.showMore}
                </PillButton>
              ) : null}
            </section>
          ) : null}
        </div>
      </main>
      <BottomNav />
    </div>
  );
}

function BalanceCard({ balances, now }: { balances: ReturnType<typeof useBalances>; now: Date }) {
  const data = balances.data;
  const headline = data ? balanceHeadline(data.net_cents) : null;
  const lines = data ? pairLines(data.pairs) : [];
  return (
    <section className="kt-balance-card" aria-labelledby="balance-heading">
      <div className="kt-balance-card__top">
        <h2 id="balance-heading" className="kt-balance-card__label">
          {strings.tabs.balance}
        </h2>
        <span>{stampDate(now)}</span>
      </div>
      {balances.isError ? (
        <ErrorCard error={balances.error} onRetry={() => void balances.refetch()} />
      ) : null}
      {!data && !balances.isError ? <Skeleton lines={3} /> : null}
      {headline ? (
        <div className="kt-balance-card__big" data-kind={headline.kind}>
          {headline.kind === "square" ? (
            <StampChip rotate={-3} large>
              {headline.text}
            </StampChip>
          ) : (
            <>
              <span className="kt-balance-card__amount">{headline.text}</span>
              <span className="kt-balance-card__words">
                {headline.kind === "owed" ? strings.tabs.comingToYou : strings.tabs.youOwe}
              </span>
            </>
          )}
        </div>
      ) : null}
      {data && lines.length > 0 ? (
        <>
          <hr className="kt-balance-card__rule" />
          <BalanceTicker lines={lines.slice(0, PAIR_ROWS)} footnote={strings.balance.netted} />
          {lines.length > PAIR_ROWS ? (
            <Link href="/balances" className="kt-text-link kt-balance-card__all">
              {strings.tabs.seeAll}
            </Link>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function OpenCard({ card, meId, now }: { card: Card; meId: string; now: Date }) {
  const stamp = tabStamp(card, meId);
  const note = changeNote(card, now);
  return (
    <Link href={`/bills/${card.id}`} className="kt-tab-card kt-open-card">
      <span className="kt-open-card__top">
        <span className="kt-open-card__titles">
          <span className="kt-tab-card__store">{card.store_name ?? strings.bill.untitled}</span>
          <span className="kt-tab-card__meta">{cardMeta(card, meId)}</span>
        </span>
        <span className="kt-tab-card__total">{formatMoney(card.total_cents)}</span>
      </span>
      {stamp || note ? (
        <span className="kt-open-card__status">
          {stamp ? (
            <StampChip tone={stamp.tone} rotate={stamp.tone === "warn" ? 1.5 : -2}>
              {stamp.text}
            </StampChip>
          ) : null}
          {note ? (
            <span className="kt-open-card__note">
              <span className="kt-unseen-dot" aria-hidden="true" />
              <span className="sr-only">{strings.tabs.unseen}: </span>
              {note}
            </span>
          ) : null}
        </span>
      ) : null}
    </Link>
  );
}

function SettledCard({ card, tilt }: { card: Card; tilt: number }) {
  const stamp = tabStamp(card, null);
  return (
    <Link href={`/bills/${card.id}`} className="kt-settled-card">
      <span>
        <b>{card.store_name ?? strings.bill.untitled}</b> · {monDay(card.purchase_date)}
      </span>
      {stamp ? (
        <StampChip tone="muted" rotate={tilt % 2 ? 3 : -4}>
          {stamp.text}
        </StampChip>
      ) : null}
    </Link>
  );
}

function Search({
  houseId,
  query,
  onQuery,
}: {
  houseId: string;
  query: string;
  onQuery: (q: string) => void;
}) {
  const id = useId();
  const hintId = useId();
  const results = useSearch(houseId, query);
  const term = query.trim();
  return (
    <div className="kt-search" role="search">
      <label htmlFor={id} className="kt-field__label">
        {strings.tabs.searchLabel}
      </label>
      <input
        id={id}
        type="search"
        className="kt-input kt-input--big"
        placeholder={strings.tabs.searchPlaceholder}
        value={query}
        maxLength={60}
        aria-describedby={hintId}
        onChange={(e) => onQuery(e.target.value)}
      />
      <div id={hintId} className="kt-search__results" aria-live="polite">
        {term.length === 1 ? <span className="kt-hint">{strings.tabs.searchHint}</span> : null}
        {term.length >= 2 && results.data ? (
          results.data.length === 0 ? (
            <span className="kt-hint">{strings.tabs.noResults(term)}</span>
          ) : (
            <>
              <span className="kt-field__label">{strings.tabs.results(results.data.length)}</span>
              <ul className="kt-list">
                {results.data.map((hit) => (
                  <li key={hit.id}>
                    <Link href={`/bills/${hit.id}`} className="kt-tab-card kt-search__hit">
                      <span className="kt-open-card__titles">
                        <span className="kt-tab-card__store">
                          {hit.store_name ?? strings.bill.untitled}
                        </span>
                        <span className="kt-tab-card__meta">
                          {monDay(hit.purchase_date)}
                          {hit.state !== "open"
                            ? ` · ${hit.state === "voided" ? strings.tabs.voided : strings.tabs.settled}`
                            : ""}
                        </span>
                      </span>
                      <span className="kt-tab-card__total">{formatMoney(hit.total_cents)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          )
        ) : null}
      </div>
    </div>
  );
}
