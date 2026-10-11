"use client";

import { useEffect, useState } from "react";

/**
 * `value` after it has stopped changing for `ms` (0 = immediately). Compared by JSON, so a new
 * object with the same contents doesn't restart the wait.
 */
export function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  const key = JSON.stringify(value);
  useEffect(() => {
    if (ms <= 0) {
      setSettled(value);
      return;
    }
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the contents
  }, [key, ms]);
  return ms <= 0 ? value : settled;
}
