import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { OTPInput } from "@/components/OTPInput";

function Harness({
  initial = "",
  onComplete,
}: {
  initial?: string;
  onComplete?: (code: string) => void;
}) {
  const [value, setValue] = useState(initial);
  return <OTPInput value={value} onChange={setValue} onComplete={onComplete} />;
}

function boxes(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll(".kt-otp__box")).map((b) => b.textContent ?? "");
}

describe("OTPInput", () => {
  it("is one real input wired for one-time-code autofill", () => {
    const { container } = render(<Harness />);
    const input = screen.getByRole("textbox", { name: "6-digit code" });
    expect(input.getAttribute("autocomplete")).toBe("one-time-code");
    expect(input.getAttribute("inputmode")).toBe("numeric");
    expect(input.getAttribute("maxlength")).toBe("6");
    expect(container.querySelectorAll("input")).toHaveLength(1);
    expect(container.querySelectorAll(".kt-otp__box")).toHaveLength(6);
  });

  it("paste fills every box, ignoring non-digits, and completes", () => {
    const onComplete = vi.fn();
    const { container } = render(<Harness onComplete={onComplete} />);
    const input = screen.getByRole("textbox");
    fireEvent.paste(input, { clipboardData: { getData: () => "Your code: 482 913" } });
    expect(boxes(container)).toEqual(["4", "8", "2", "9", "1", "3"]);
    expect((input as HTMLInputElement).value).toBe("482913");
    expect(onComplete).toHaveBeenCalledWith("482913");
  });

  it("paste of a longer string keeps only the first six digits", () => {
    const { container } = render(<Harness />);
    fireEvent.paste(screen.getByRole("textbox"), { clipboardData: { getData: () => "12345678" } });
    expect(boxes(container)).toEqual(["1", "2", "3", "4", "5", "6"]);
  });

  it("backspace removes the last digit, one at a time", () => {
    const { container } = render(<Harness initial="482" />);
    const input = screen.getByRole("textbox");
    fireEvent.keyDown(input, { key: "Backspace" });
    expect(boxes(container)).toEqual(["4", "8", "", "", "", ""]);
    fireEvent.keyDown(input, { key: "Backspace" });
    fireEvent.keyDown(input, { key: "Backspace" });
    fireEvent.keyDown(input, { key: "Backspace" });
    expect(boxes(container)).toEqual(["", "", "", "", "", ""]);
  });

  it("typing filters to digits and marks the next box active while focused", () => {
    const { container } = render(<Harness />);
    const input = screen.getByRole("textbox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "4a8" } });
    expect(boxes(container).slice(0, 3)).toEqual(["4", "8", ""]);
    const states = Array.from(container.querySelectorAll(".kt-otp__box")).map((b) =>
      b.getAttribute("data-state"),
    );
    expect(states).toEqual(["filled", "filled", "active", "empty", "empty", "empty"]);
  });

  it("links the error message and marks the input invalid", () => {
    render(<OTPInput value="111111" onChange={() => undefined} error="That code doesn't match." />);
    const input = screen.getByRole("textbox");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    const describedBy = input.getAttribute("aria-describedby")!;
    expect(document.getElementById(describedBy)?.textContent).toBe("That code doesn't match.");
  });
});
