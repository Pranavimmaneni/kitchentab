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

  it("A11Y-02 pauses the 4 s timer while hovered and resumes with the time that was left (WCAG 2.2.1)", () => {
    const onDismiss = vi.fn();
    render(<Toast message="Arjun removed from Coffee beans." onUndo={() => undefined} onDismiss={onDismiss} />);
    const toast = screen.getByText("Arjun removed from Coffee beans.").parentElement!;
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    fireEvent.mouseEnter(toast);
    expect(toast.getAttribute("data-paused")).toBe("true");
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(onDismiss).not.toHaveBeenCalled();
    fireEvent.mouseLeave(toast);
    act(() => {
      vi.advanceTimersByTime(999);
    });
    expect(onDismiss).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("A11Y-02 pauses while keyboard focus is inside (Undo reachable by Tab) and resumes on blur", () => {
    const onDismiss = vi.fn();
    render(
      <>
        <Toast message="Kabir removed from Eggs." onUndo={() => undefined} onDismiss={onDismiss} />
        <button type="button">Elsewhere</button>
      </>,
    );
    const undo = screen.getByRole("button", { name: "Undo" });
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    act(() => {
      undo.focus();
    });
    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    expect(onDismiss).not.toHaveBeenCalled();
    act(() => {
      screen.getByRole("button", { name: "Elsewhere" }).focus();
    });
    act(() => {
      vi.advanceTimersByTime(1999);
    });
    expect(onDismiss).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("A11Y-02 a toast that disappears while hovered does not leave the next one paused", () => {
    const onDismiss = vi.fn();
    const { rerender } = render(<Toast message="First." onDismiss={onDismiss} />);
    fireEvent.mouseEnter(screen.getByText("First.").parentElement!);
    rerender(<Toast message={null} onDismiss={onDismiss} />);
    rerender(<Toast message="Second." onDismiss={onDismiss} />);
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
