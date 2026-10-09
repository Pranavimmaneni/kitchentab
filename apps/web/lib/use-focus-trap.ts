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
 * Safari does not focus buttons on click, so document.activeElement is <body> when a sheet
 * opens from a tap. Remember the last control the user pressed as the fallback opener.
 */
let lastPressed: HTMLElement | null = null;
let tracking = false;

function trackPresses(): void {
  if (tracking || typeof document === "undefined") return;
  tracking = true;
  document.addEventListener(
    "pointerdown",
    (event) => {
      const target =
        event.target instanceof Element ? event.target.closest<HTMLElement>(FOCUSABLE) : null;
      if (target) lastPressed = target;
    },
    true,
  );
}

function openerFor(container: HTMLElement): HTMLElement | null {
  const active = document.activeElement;
  if (active instanceof HTMLElement && active !== document.body && !container.contains(active)) {
    return active;
  }
  if (lastPressed && lastPressed.isConnected && !container.contains(lastPressed))
    return lastPressed;
  return null;
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
  useEffect(trackPresses, []);

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;
    const previouslyFocused = openerFor(container);

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
