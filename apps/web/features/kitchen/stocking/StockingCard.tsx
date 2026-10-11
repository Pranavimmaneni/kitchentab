"use client";

/**
 * Review & send: the food on this bill above three bins, and the stocking animation that runs
 * while Send is in flight (SPEC §7, FR-G10; renders sort-before, sort-after, sort-storyboard).
 * Transform and opacity only, through the Web Animations API, so nothing reflows mid-flight.
 */
import { useImperativeHandle, useRef, useState, type Ref } from "react";
import { FoodIcon, foodPastel } from "@/components/FoodIcon";
import type { BillItem } from "@/lib/api/types";
import type { Place } from "@/lib/api/kitchen-types";
import { formatMoney } from "@/lib/format";
import { haptic } from "@/lib/haptics";
import { strings } from "@/lib/strings";
import { prefersReducedMotion } from "@/lib/use-focus-trap";
import { PlaceIcon } from "../gestures/GestureLayer";
import {
  BADGE_MS,
  DIM_MS,
  EASE_LIFT,
  EASE_X,
  EASE_Y,
  FADE_MS,
  FLY_BACK_MS,
  FLY_MS,
  LIFT_MS,
  SQUASH_MS,
  flyStart,
  landAt,
  scatter,
  totalMs,
} from "./timing";

const BINS: readonly Place[] = ["freezer", "fridge", "pantry"];

export type StockingHandle = {
  /** Runs the animation; resolves when the last icon has landed and faded. */
  play: () => Promise<void>;
  /** The send failed: icons fly home, bins reset, nothing was stocked. */
  flyBack: () => Promise<void>;
};

const isPlace = (s: string): s is Place => s === "fridge" || s === "freezer" || s === "pantry";

export function stockable(items: readonly BillItem[]): BillItem[] {
  return items.filter((i) => isPlace(i.storage));
}

export function StockingCard({
  items,
  currency,
  ref,
}: {
  items: readonly BillItem[];
  currency: string;
  ref?: Ref<StockingHandle>;
}) {
  const food = stockable(items);
  const root = useRef<HTMLDivElement>(null);
  const running = useRef<Animation[]>([]);
  const timers = useRef<number[]>([]);
  const [landed, setLanded] = useState<Partial<Record<Place, number>>>({});
  const [dim, setDim] = useState(false);

  const clearTimers = () => {
    for (const t of timers.current) window.clearTimeout(t);
    timers.current = [];
  };

  useImperativeHandle(ref, () => ({
    async play() {
      const el = root.current;
      clearTimers();
      setLanded({});
      if (!el || food.length === 0) return;
      const rows = [...el.querySelectorAll<HTMLElement>("[data-stock-row]")];
      const n = rows.length;
      setDim(true);
      if (prefersReducedMotion()) {
        // No flight: rows dim, counts fade in, one haptic (ANIM-03).
        const counts: Partial<Record<Place, number>> = {};
        for (const item of food)
          counts[item.storage as Place] = (counts[item.storage as Place] ?? 0) + 1;
        setLanded(counts);
        haptic("success");
        await new Promise((r) => window.setTimeout(r, DIM_MS));
        return;
      }
      const bins = new Map(
        [...el.querySelectorAll<HTMLElement>("[data-stock-bin]")].map((b) => [
          b.dataset.stockBin as Place,
          b,
        ]),
      );
      const caught = new Set<Place>();
      const all: Animation[] = [];
      rows.forEach((row, i) => {
        const place = row.dataset.stockRow as Place;
        const x = row.querySelector<HTMLElement>("[data-fly-x]");
        const y = row.querySelector<HTMLElement>("[data-fly-y]");
        const bin = bins.get(place);
        if (!x || !y || !bin) return;
        const from = y.getBoundingClientRect();
        const to = bin.getBoundingClientRect();
        const spread = scatter(i);
        const dx = to.left + to.width / 2 - (from.left + from.width / 2) + spread.dx;
        const dy = to.top + to.height / 2 - (from.top + from.height / 2) + spread.dy;
        const start = flyStart(i, n);
        const land = landAt(i, n);
        // Lift (0–60 ms, overshoot), hold, then fly on an arc: Y eases in here, X eases out on the
        // outer element.
        all.push(
          y.animate(
            [
              { transform: "translateY(0) scale(1) rotate(0deg)", easing: EASE_LIFT },
              { transform: "translateY(-5px) scale(1.15) rotate(0deg)", offset: LIFT_MS / land },
              {
                transform: "translateY(-5px) scale(1.15) rotate(0deg)",
                offset: start / land,
                easing: EASE_Y,
              },
              { transform: `translateY(${dy}px) scale(0.62) rotate(${spread.rotate}deg)` },
            ],
            { duration: land, fill: "forwards" },
          ),
          x.animate([{ transform: "translateX(0)" }, { transform: `translateX(${dx}px)` }], {
            delay: start,
            duration: FLY_MS,
            easing: EASE_X,
            fill: "forwards",
          }),
          x.animate([{ opacity: 1 }, { opacity: 0 }], {
            delay: land,
            duration: FADE_MS,
            fill: "forwards",
          }),
        );
        timers.current.push(
          window.setTimeout(() => {
            if (!caught.has(place)) {
              caught.add(place);
              bin.animate(
                [
                  { transform: "scale(1)" },
                  { transform: "scale(1.08, 0.92)" },
                  { transform: "scale(1)" },
                ],
                {
                  duration: SQUASH_MS,
                },
              );
            }
            setLanded((now) => ({ ...now, [place]: (now[place] ?? 0) + 1 }));
          }, land),
        );
      });
      timers.current.push(window.setTimeout(() => haptic("success"), landAt(n - 1, n)));
      running.current = all;
      // A hidden page pauses the animation clock; never hold Send hostage to it.
      await Promise.race([
        Promise.all(all.map((a) => a.finished.catch(() => undefined))),
        new Promise((r) => window.setTimeout(r, totalMs(n) + 150)),
      ]);
    },

    async flyBack() {
      clearTimers();
      const el = root.current;
      const anims = running.current;
      running.current = [];
      if (!el) return;
      // From wherever each icon is now, home in 200 ms.
      const back: Promise<unknown>[] = [];
      for (const node of el.querySelectorAll<HTMLElement>("[data-fly-x], [data-fly-y]")) {
        const style = getComputedStyle(node);
        const now = {
          transform: style.transform === "none" ? "none" : style.transform,
          opacity: style.opacity,
        };
        for (const a of node.getAnimations()) a.cancel();
        back.push(
          node
            .animate([now, { transform: "none", opacity: "1" }], {
              duration: FLY_BACK_MS,
              easing: EASE_X,
            })
            .finished.catch(() => undefined),
        );
      }
      for (const a of anims) a.cancel();
      setLanded({});
      setDim(false);
      await Promise.all(back);
    },
  }));

  if (food.length === 0) return null;
  return (
    <div className="kt-stocking" ref={root} data-dim={dim || undefined}>
      <ul className="kt-stocking__rows" aria-label={strings.send.stockingLabel}>
        {food.map((item) => (
          <li key={item.id} className="kt-stock-row" data-stock-row={item.storage}>
            {/* The dashed slot stays behind when the icon flies off (sort-after). */}
            <span className="kt-stock-row__slot">
              <span className="kt-stock-row__fly" data-fly-x="">
                <span
                  className="kt-stock-row__icon"
                  data-fly-y=""
                  style={{ background: foodPastel(item.icon_key, item.storage as Place) }}
                >
                  <FoodIcon iconKey={item.icon_key} place={item.storage as Place} />
                </span>
              </span>
            </span>
            <span className="kt-stock-row__name">{item.name}</span>
            <span className="kt-stock-row__tag" data-place={item.storage}>
              {strings.item.places[item.storage as Place]}
            </span>
            <span className="kt-stock-row__price kt-money">
              {formatMoney(item.line_total_cents, currency)}
            </span>
          </li>
        ))}
      </ul>
      <div className="kt-stocking__bins">
        {BINS.map((p) => {
          const count = landed[p] ?? 0;
          return (
            <div key={p} className="kt-stock-bin" data-stock-bin={p} data-place={p}>
              <PlaceIcon place={p} size={28} />
              <span className="kt-stock-bin__name">{strings.item.places[p]}</span>
              {count > 0 ? (
                <span
                  className="kt-stock-bin__count"
                  style={{ ["--badge-ms" as string]: `${BADGE_MS}ms` }}
                >
                  +{count}
                </span>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
