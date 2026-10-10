"use client";

import { useEffect, useState } from "react";
import { onServerOffset, serverNow } from "./server-time";

/**
 * Server "now" for relative times, refreshed every `everyMs` and as soon as the first answer
 * (or a clock jump) changes the offset.
 */
export function useServerNow(everyMs = 30_000): number {
  const [now, setNow] = useState(serverNow);
  useEffect(() => {
    const tick = () => setNow(serverNow());
    const off = onServerOffset(tick);
    const timer = window.setInterval(tick, everyMs);
    tick();
    return () => {
      off();
      window.clearInterval(timer);
    };
  }, [everyMs]);
  return now;
}
