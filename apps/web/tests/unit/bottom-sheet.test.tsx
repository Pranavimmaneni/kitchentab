import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { BottomSheet } from "@/components/BottomSheet";
import { Dialog } from "@/components/Dialog";
import { mediaState } from "./setup";

function SheetHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Pay Pranav
      </button>
      <BottomSheet open={open} onClose={() => setOpen(false)} title="Pay Pranav $33.91">
        <button type="button">Copy amount</button>
        <button type="button">Venmo</button>
      </BottomSheet>
    </>
  );
}

describe("BottomSheet", () => {
  it("opens as a labelled modal dialog and moves focus inside", () => {
    render(<SheetHarness />);
    fireEvent.click(screen.getByRole("button", { name: "Pay Pranav" }));
    const sheet = screen.getByRole("dialog", { name: "Pay Pranav $33.91" });
    expect(sheet.getAttribute("aria-modal")).toBe("true");
    expect(sheet.contains(document.activeElement)).toBe(true);
  });

  it("Esc closes it and focus returns to the opener", () => {
    render(<SheetHarness />);
    const opener = screen.getByRole("button", { name: "Pay Pranav" });
    opener.focus();
    fireEvent.click(opener);
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("returns focus to the tapped opener even when the click did not focus it (Safari)", () => {
    render(<SheetHarness />);
    const opener = screen.getByRole("button", { name: "Pay Pranav" });
    (document.activeElement as HTMLElement | null)?.blur();
    fireEvent.pointerDown(opener);
    fireEvent.click(opener);
    expect(document.activeElement).not.toBe(opener);
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("traps Tab inside the sheet in both directions", () => {
    render(<SheetHarness />);
    fireEvent.click(screen.getByRole("button", { name: "Pay Pranav" }));
    const close = screen.getByRole("button", { name: "Close" });
    const last = screen.getByRole("button", { name: "Venmo" });
    expect(document.activeElement).toBe(close);
    fireEvent.keyDown(document.activeElement!, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
    fireEvent.keyDown(document.activeElement!, { key: "Tab" });
    expect(document.activeElement).toBe(close);
  });

  it("with motion allowed it plays the closing state, then unmounts and returns focus", () => {
    mediaState.reducedMotion = false;
    render(<SheetHarness />);
    const opener = screen.getByRole("button", { name: "Pay Pranav" });
    opener.focus();
    fireEvent.click(opener);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    const root = document.querySelector(".kt-sheet")!;
    expect(root.getAttribute("data-state")).toBe("closing");
    expect(document.activeElement).toBe(opener);
    act(() => {
      const panel = document.querySelector(".kt-sheet__panel")!;
      // React listens for whichever animationend name the environment supports.
      for (const type of ["animationend", "webkitAnimationEnd"]) {
        panel.dispatchEvent(new Event(type, { bubbles: true }));
      }
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

function DialogHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Stamp it settled
      </button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Stamp it settled?"
        confirmLabel="Stamp it"
        onConfirm={() => setOpen(false)}
      >
        Everyone&apos;s square.
      </Dialog>
    </>
  );
}

describe("Dialog", () => {
  it("is labelled by its title, described by its body, closes on Esc and returns focus", () => {
    render(<DialogHarness />);
    const opener = screen.getByRole("button", { name: "Stamp it settled" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Stamp it settled?" });
    const describedBy = dialog.getAttribute("aria-describedby")!;
    expect(document.getElementById(describedBy)?.textContent).toBe("Everyone's square.");
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});
