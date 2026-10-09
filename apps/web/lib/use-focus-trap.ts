"use client";

import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusables(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.hasAttribute("inert") && el.getAttribute("aria-hidden") !== "true",
  );
}

/**
 * Traps Tab inside `containerRef` while active, closes on Escape and returns focus to the
 * element that was focused before activation.
 */
export function useFocusTrap(
  containerRef: RefObject<HTMLElement | null>,
  active: boolean,
  onEscape: () => void,
  initialFocusRef?: RefObject<HTMLElement | null>,
): void {
  const escapeRef = useRef(onEscape);
  useEffect(() => {
    escapeRef.current = onEscape;
  }, [onEscape]);

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;
    const previouslyFocused =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    const first = initialFocusRef?.current ?? focusables(container)[0] ?? container;
    first.focus();

    function onKeyDown(event: KeyboardEvent): void {
      if (!container) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        escapeRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusables(container);
      if (items.length === 0) {
        event.preventDefault();
        container.focus();
        return;
      }
      const firstItem = items[0]!;
      const lastItem = items[items.length - 1]!;
      const current = document.activeElement;
      if (event.shiftKey && (current === firstItem || !container.contains(current))) {
        event.preventDefault();
        lastItem.focus();
      } else if (!event.shiftKey && (current === lastItem || !container.contains(current))) {
        event.preventDefault();
        firstItem.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      if (previouslyFocused && previouslyFocused.isConnected) previouslyFocused.focus();
    };
  }, [active, containerRef, initialFocusRef]);
}

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  return !window.matchMedia("(prefers-reduced-motion: no-preference)").matches;
}
