"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type PointerEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { prefersReducedMotion, useFocusTrap } from "@/lib/use-focus-trap";
import { strings } from "@/lib/strings";
import { XIcon } from "./icons";

export type BottomSheetProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  /** Actions at the bottom of the sheet. */
  footer?: ReactNode;
  initialFocusRef?: RefObject<HTMLElement | null>;
};

const DRAG_CLOSE_PX = 80;

/**
 * Paper sheet with a 2 px top border, 16 px top corners and a drag handle (PRD §14B).
 * Focus is trapped while open, Esc closes, and focus returns to the opener.
 */
export function BottomSheet({
  open,
  onClose,
  title,
  children,
  footer,
  initialFocusRef,
}: BottomSheetProps) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const [rendered, setRendered] = useState(open);
  const [closing, setClosing] = useState(false);
  const [dragY, setDragY] = useState(0);
  const dragStart = useRef<number | null>(null);

  useEffect(() => {
    if (open) {
      setRendered(true);
      setClosing(false);
    } else if (rendered) {
      if (prefersReducedMotion()) setRendered(false);
      else setClosing(true);
    }
  }, [open, rendered]);

  useEffect(() => {
    if (!rendered || closing) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [rendered, closing]);

  useFocusTrap(panelRef, rendered && !closing, onClose, initialFocusRef);

  if (!rendered) return null;

  function onPointerDown(event: PointerEvent<HTMLDivElement>): void {
    dragStart.current = event.clientY;
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  function onPointerMove(event: PointerEvent<HTMLDivElement>): void {
    if (dragStart.current === null) return;
    setDragY(Math.max(0, event.clientY - dragStart.current));
  }
  function onPointerUp(): void {
    const shouldClose = dragY > DRAG_CLOSE_PX;
    dragStart.current = null;
    setDragY(0);
    if (shouldClose) onClose();
  }

  return (
    <div className="kt-sheet" data-state={closing ? "closing" : "open"}>
      <div className="kt-sheet__backdrop" aria-hidden="true" onClick={onClose} />
      <div
        ref={panelRef}
        className="kt-sheet__panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        style={dragY ? { transform: `translateY(${dragY}px)` } : undefined}
        onAnimationEnd={(event) => {
          if (closing && event.target === event.currentTarget) {
            setClosing(false);
            setRendered(false);
          }
        }}
      >
        <div
          className="kt-sheet__handle"
          aria-hidden="true"
          title={strings.sheet.dragHandle}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          <span />
        </div>
        <div className="kt-sheet__header">
          <h2 id={titleId} className="kt-sheet__title">
            {title}
          </h2>
          <button
            type="button"
            className="kt-icon-button"
            aria-label={strings.sheet.close}
            onClick={onClose}
          >
            <XIcon size={18} />
          </button>
        </div>
        <div className="kt-sheet__body">{children}</div>
        {footer ? <div className="kt-sheet__footer">{footer}</div> : null}
      </div>
    </div>
  );
}
