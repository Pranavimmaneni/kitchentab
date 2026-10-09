import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TOAST_DURATION_MS, Toast } from "@/components/Toast";

describe("Toast", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps a polite live region mounted even with no message", () => {
    render(<Toast message={null} />);
    const region = screen.getByRole("status");
    expect(region.getAttribute("aria-live")).toBe("polite");
    expect(region.getAttribute("aria-atomic")).toBe("true");
    expect(region.textContent).toBe("");
  });

  it("renders the message inside the live region so it is announced", () => {
    const { rerender } = render(<Toast message={null} />);
    rerender(<Toast message="Marked. Pranav will confirm." />);
    expect(screen.getByRole("status").textContent).toContain("Marked. Pranav will confirm.");
  });

  it("dismisses itself after 4 seconds", () => {
    const onDismiss = vi.fn();
    render(<Toast message="Saved." onDismiss={onDismiss} />);
    expect(TOAST_DURATION_MS).toBe(4000);
    act(() => {
      vi.advanceTimersByTime(3999);
    });
    expect(onDismiss).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("Undo calls onUndo and dismisses", () => {
    const onUndo = vi.fn();
    const onDismiss = vi.fn();
    render(
      <Toast message="Arjun removed from Coffee beans." onUndo={onUndo} onDismiss={onDismiss} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(onUndo).toHaveBeenCalledTimes(1);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
