"use client";

import { useCallback, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { formatMoney } from "@/lib/format";
import { initialOf } from "@/lib/members";
import { strings } from "@/lib/strings";
import { MemberAvatar } from "./MemberAvatar";
import { Skeleton } from "./Skeleton";
import { SplitCheckbox } from "./SplitCheckbox";

export type SplitMember = {
  id: string;
  name: string;
  /** Short header tag, e.g. "YOU", "ARJ". */
  tag: string;
  initial?: string;
  /** 0-based join order (color slot). */
  colorIndex: number;
};

export type SplitItem = {
  id: string;
  name: string;
  priceCents: number;
  /** Server-provided detail after the price, e.g. "$1.43 × 3" or "JUST MEERA". */
  note?: string;
};

export type SplitTotal = { memberId: string; amountCents: number };

export type SplitGridMode = "payer" | "member" | "readonly";

export type SplitGridProps = {
  members: readonly SplitMember[];
  items: readonly SplitItem[];
  /** itemId → member ids who use it. */
  assigned: Readonly<Record<string, readonly string[]>>;
  /** Cells waiting for server ack, keyed `${itemId}:${memberId}`. */
  pending?: ReadonlySet<string>;
  /** Per-member totals from the server. The grid never computes money. */
  totals?: readonly SplitTotal[];
  mode?: SplitGridMode;
  /** Member mode: the signed-in member, whose column is the only editable one. */
  currentMemberId?: string;
  onToggle?: (itemId: string, memberId: string, next: boolean) => void;
  loading?: boolean;
  /** Shows the "someone else changed this" notice with a refresh action. */
  conflict?: ReactNode;
  /** Line under the rows, e.g. the tax note. */
  note?: string;
  /** Action under the totals (e.g. "All sorted → review" or the items-left pill). */
  footer?: ReactNode;
  currency?: string;
  label?: string;
};

/** More members than this and the checkbox columns scroll sideways with the item column sticky. */
export const MAX_FIXED_COLUMNS = 5;

type Cell = { row: number; col: number };

/**
 * Sticky member header, item rows of SplitCheckboxes and a sticky totals bar (PRD §14B).
 * Arrow keys move focus between checkboxes in a roving-tabindex grid.
 */
export function SplitGrid({
  members,
  items,
  assigned,
  pending,
  totals = [],
  mode = "payer",
  currentMemberId,
  onToggle,
  loading = false,
  conflict,
  note,
  footer,
  currency = "USD",
  label = strings.split.gridLabel,
}: SplitGridProps) {
  const wide = members.length > MAX_FIXED_COLUMNS;
  const headerScrollRef = useRef<HTMLDivElement>(null);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const [active, setActive] = useState<Cell | null>(null);

  const editableCol = useCallback(
    (col: number): boolean => {
      if (mode === "readonly") return false;
      if (mode === "member") return members[col]?.id === currentMemberId;
      return true;
    },
    [mode, members, currentMemberId],
  );

  const firstEditable = useMemo<Cell | null>(() => {
    if (items.length === 0) return null;
    const col = members.findIndex((_, c) => editableCol(c));
    return col === -1 ? null : { row: 0, col };
  }, [items.length, members, editableCol]);

  const tabStop =
    active && active.row < items.length && editableCol(active.col) ? active : firstEditable;

  const focusCell = useCallback((cell: Cell) => {
    setActive(cell);
    buttons.current.get(`${cell.row}:${cell.col}`)?.focus();
  }, []);

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>, from: Cell) => {
      const cols = members.map((_, c) => c).filter(editableCol);
      if (cols.length === 0) return;
      const lastRow = items.length - 1;
      const at = cols.indexOf(from.col);
      let next: Cell | null = null;
      switch (event.key) {
        case "ArrowRight":
          next = at < cols.length - 1 ? { row: from.row, col: cols[at + 1]! } : null;
          break;
        case "ArrowLeft":
          next = at > 0 ? { row: from.row, col: cols[at - 1]! } : null;
          break;
        case "ArrowDown":
          next = from.row < lastRow ? { row: from.row + 1, col: from.col } : null;
          break;
        case "ArrowUp":
          next = from.row > 0 ? { row: from.row - 1, col: from.col } : null;
          break;
        case "Home":
          next = { row: event.ctrlKey ? 0 : from.row, col: cols[0]! };
          break;
        case "End":
          next = { row: event.ctrlKey ? lastRow : from.row, col: cols[cols.length - 1]! };
          break;
        default:
          return;
      }
      event.preventDefault();
      if (next) focusCell(next);
    },
    [members, items.length, editableCol, focusCell],
  );

  const totalFor = new Map(totals.map((t) => [t.memberId, t.amountCents]));

  return (
    <div
      className="kt-split"
      data-wide={wide || undefined}
      style={{ ["--cols" as string]: members.length }}
    >
      {conflict ? (
        <div className="kt-split__conflict" role="status">
          {conflict}
        </div>
      ) : null}
      <div
        role="grid"
        aria-label={label}
        aria-readonly={mode === "readonly" || undefined}
        aria-busy={loading || undefined}
        className="kt-split__grid"
      >
        <div className="kt-split__head" ref={headerScrollRef}>
          <div role="row" className="kt-split__row kt-split__row--head">
            <span role="columnheader" className="kt-split__item kt-split__item--head">
              {strings.split.item}
            </span>
            {members.map((m) => (
              <span role="columnheader" key={m.id} className="kt-split__member" aria-label={m.name}>
                <MemberAvatar
                  name={m.name}
                  initial={m.initial ?? initialOf(m.name)}
                  colorIndex={m.colorIndex}
                  size={26}
                />
                <span className="kt-split__tag" aria-hidden="true">
                  <span className="kt-split__tag-short">{m.tag}</span>
                  <span className="kt-split__tag-long">{m.name}</span>
                </span>
              </span>
            ))}
          </div>
        </div>
        {loading ? null : (
          <div
            className="kt-split__body"
            onScroll={(event) => {
              if (headerScrollRef.current)
                headerScrollRef.current.scrollLeft = event.currentTarget.scrollLeft;
            }}
          >
            {items.map((item, row) => {
              const users = assigned[item.id] ?? [];
              return (
                <div role="row" key={item.id} className="kt-split__row">
                  <div role="rowheader" className="kt-split__item">
                    <span className="kt-split__name">{item.name}</span>
                    <span className="kt-split__detail">
                      <span className="kt-money">{formatMoney(item.priceCents, currency)}</span>
                      {" · "}
                      {users.length === 0 ? (
                        <span className="kt-split__nobody">{strings.split.nobodyYet}</span>
                      ) : (
                        <span className="kt-money">{item.note}</span>
                      )}
                    </span>
                  </div>
                  {members.map((m, col) => {
                    const checked = users.includes(m.id);
                    const isStop = tabStop?.row === row && tabStop.col === col;
                    return (
                      <div role="gridcell" key={m.id} className="kt-split__cell">
                        <SplitCheckbox
                          buttonRef={(el) => {
                            const key = `${row}:${col}`;
                            if (el) buttons.current.set(key, el);
                            else buttons.current.delete(key);
                          }}
                          memberName={m.name}
                          itemName={item.name}
                          initial={m.initial ?? initialOf(m.name)}
                          colorIndex={m.colorIndex}
                          checked={checked}
                          row={row}
                          col={col}
                          disabled={!editableCol(col)}
                          pending={pending?.has(`${item.id}:${m.id}`)}
                          tabIndex={isStop ? 0 : -1}
                          onFocus={() => setActive({ row, col })}
                          onKeyDown={(event) => onKeyDown(event, { row, col })}
                          onToggle={() => onToggle?.(item.id, m.id, !checked)}
                        />
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        )}
      </div>
      {loading ? (
        <div className="kt-split__loading">
          <Skeleton lines={6} />
        </div>
      ) : null}
      {note ? <p className="kt-split__note">{note}</p> : null}
      {loading && !footer ? null : (
        <div className="kt-split__totals">
          {loading ? null : (
            <div
              className="kt-split__sums"
              role="group"
              aria-label={strings.split.totalsLabel}
              tabIndex={wide ? 0 : undefined}
            >
              <dl className="kt-split__sums-list">
                {members.map((m) => (
                  <div key={m.id} className="kt-split__sum">
                    <dt>{m.tag}</dt>
                    <dd className="kt-money">{formatMoney(totalFor.get(m.id) ?? 0, currency)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
          {footer}
        </div>
      )}
    </div>
  );
}
