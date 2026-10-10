"use client";

import { useEffect, useState } from "react";
import { writesInFlight } from "@/lib/api/signals";
import { strings } from "@/lib/strings";

/** Writes that answer faster than this never flash the indicator. */
export const SAVING_DELAY_MS = 300;

/**
 * "Saving…" while a write is slow (PRD §21.6 Retries, CHAOS-06): shown once any write has been in
 * flight for 300 ms, gone when the last one ends. A failed write then says "Couldn't save · Retry"
 * on the screen that made it. Never blocks a tap.
 */
export function SavingIndicator() {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    let timer: number | null = null;
    const update = () => {
      if (writesInFlight() > 0) {
        timer ??= window.setTimeout(() => {
          timer = null;
          if (writesInFlight() > 0) setVisible(true);
        }, SAVING_DELAY_MS);
      } else {
        if (timer !== null) window.clearTimeout(timer);
        timer = null;
        setVisible(false);
      }
    };
    window.addEventListener("kt:writes", update);
    update();
    return () => {
      window.removeEventListener("kt:writes", update);
      if (timer !== null) window.clearTimeout(timer);
    };
  }, []);
  return (
    <div className="kt-saving" role="status" data-visible={visible || undefined}>
      {visible ? strings.error.saving : null}
    </div>
  );
}
